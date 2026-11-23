import platform
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import psutil

from .alignment import align, apply_roles, coalesce
from .intelligence import audit_candidates, extract, infer_roles
from .io import write_json
from .offline import offline_mode
from .schemas import RoleResult, Transcript


class NeedsRoleConfirmation(RuntimeError):
    pass


class Run:
    def __init__(self, output: Path, mode: str):
        output.mkdir(parents=True, exist_ok=False)
        self.output = output
        self.report = {
            "schema_version": "1.0",
            "mode": mode,
            "status": "running",
            "requires_human_review": True,
            "stages": {},
            "platform": platform.platform(),
            "python": platform.python_version(),
            "accuracy_measured": False,
        }
        self.started = time.perf_counter()
        self.peak = 0
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self):
        while not self.stopped.is_set():
            try:
                process = psutil.Process()
                total = process.memory_info().rss
                for child in process.children(recursive=True):
                    try:
                        total += child.memory_info().rss
                    except psutil.Error:
                        pass
                self.peak = max(self.peak, total)
            except psutil.Error:
                pass
            self.stopped.wait(0.05)

    def __enter__(self):
        self.thread.start()
        self.save()
        return self

    def save(self):
        write_json(self.output / "quality_report.json", self.report)

    @contextmanager
    def stage(self, name):
        start = time.perf_counter()
        state = {"status": "running"}
        self.report["stages"][name] = state
        self.save()
        print(name, flush=True)
        try:
            yield
        except BaseException as error:
            state["status"] = (
                "cancelled" if isinstance(error, KeyboardInterrupt) else "failed"
            )
            raise
        else:
            state["status"] = "completed"
        finally:
            state["seconds"] = round(time.perf_counter() - start, 4)
            self.save()

    def __exit__(self, kind, error, traceback):
        self.stopped.set()
        self.thread.join()
        self.report.update(
            status=(
                "awaiting_roles"
                if isinstance(error, NeedsRoleConfirmation)
                else "cancelled"
                if kind and issubclass(kind, KeyboardInterrupt)
                else "failed"
                if kind
                else "completed"
            ),
            elapsed_seconds=round(time.perf_counter() - self.started, 4),
            peak_process_tree_rss_bytes_sampled=self.peak,
            memory_sampling_interval_ms=50,
            memory_limitations="RSS includes children; GPU VRAM not measured; brief peaks may be missed",
        )
        if error:
            self.report["error"] = f"{type(error).__name__}: {error}"
        self.save()
        return False


def analyze(
    transcript,
    llm,
    run,
    threshold=0.85,
    confirmed=None,
    chunk_chars=5000,
    pause_for_roles=False,
    profile=None,
):
    with run.stage("roles"):
        known = {s.speaker_id for s in transcript.segments if s.speaker_id}
        if confirmed and known.issubset(confirmed):
            proposed = RoleResult(speakers=[])
            run.report["roles_source"] = "provided_mapping"
        else:
            proposed = infer_roles(transcript.segments, llm)
            run.report["roles_source"] = "local_llm"
        write_json(run.output / "roles_proposed.json", proposed)
        transcript.segments = apply_roles(
            transcript.segments, proposed, threshold, confirmed
        )
        write_json(run.output / "transcript.json", transcript)
        if confirmed:
            write_json(
                run.output / "roles_confirmed.json",
                {k: v.value for k, v in confirmed.items()},
            )
    pending = {
        s.speaker_id
        for s in transcript.segments
        if s.speaker_id
        and (s.speaker_role.value == "unknown" or s.role_confidence < threshold)
    }
    if pause_for_roles and pending:
        run.report["speakers_requiring_confirmation"] = sorted(pending)
        raise NeedsRoleConfirmation(
            "Подтвердите роли говорящих и повторите анализ сохранённой транскрипции"
        )
    with run.stage("facts"):
        facts = extract(
            transcript.segments,
            llm,
            chunk_chars,
            checkpoint=lambda index, part: write_json(
                run.output / f"facts_batch_{index:04d}.json", part
            ),
        )
        write_json(run.output / "facts.json", facts)
    run.report.update(
        segments=len(transcript.segments),
        people=len(facts.people),
        facts=len(facts.facts),
        conflicts=len(facts.conflicts),
        uncertain_segments=sum(s.requires_review for s in transcript.segments),
        facts_requiring_review=sum(f.requires_review for f in facts.facts),
        source_sha256=transcript.source_sha256,
        source_kind=transcript.source_kind,
        audit=audit_candidates(facts.facts),
        limitations=[
            "Цитаты проверены на вхождение, но это не доказательство истинности или полноты.",
            "Автоматическая проверка семантической привязки и качества моделей не заменяет эксперта.",
            "WER, DER, полнота и точность привязки не измерены.",
        ],
    )
    with run.stage("documents"):
        from .documents import export_documents

        run.report["documents"] = export_documents(
            transcript, facts, run.output, profile
        )
    return facts


def run_audio(source, settings, output, confirmed=None, profile=None):
    from .audio import prepare
    from .backends import LocalLLM, diarize, transcribe

    with Run(output, "audio_models") as run, offline_mode():
        with run.stage("audio_preparation"):
            metadata = prepare(
                source, output / "prepared.wav", settings.ffmpeg, settings.ffprobe
            )
            write_json(output / "source.json", metadata)
            write_json(output / "settings.json", settings)
        with run.stage("diarization"):
            turns = diarize(output / "prepared.wav", settings)
            if not turns:
                raise ValueError("Диаризация не обнаружила речь")
            write_json(output / "diarization.json", [t.model_dump() for t in turns])
        with run.stage("transcription"):
            raw = transcribe(output / "prepared.wav", settings)
            write_json(
                output / "asr_raw.json", [s.model_dump(mode="json") for s in raw]
            )
            transcript = Transcript(
                source_sha256=metadata["sha256"],
                duration_seconds=metadata["duration_seconds"],
                segments=coalesce(align(raw, turns)),
            )
            write_json(output / "transcript.json", transcript)
        with run.stage("llm_loading"):
            llm = LocalLLM(settings)
        try:
            analyze(
                transcript,
                llm,
                run,
                settings.role_threshold,
                confirmed,
                settings.chunk_chars,
                pause_for_roles=True,
                profile=profile,
            )
        finally:
            llm.close()


def analyze_saved(transcript_path, settings, output, confirmed=None, profile=None):
    from .backends import LocalLLM

    transcript = Transcript.model_validate_json(
        transcript_path.read_text(encoding="utf-8")
    )
    if not transcript.segments:
        raise ValueError("Транскрипция пуста")
    with Run(output, "saved_transcript_models") as run, offline_mode():
        import json

        for folder in (transcript_path.parent, transcript_path.parent.parent):
            metadata = folder / "source.json"
            if metadata.is_file():
                write_json(
                    output / "source.json",
                    json.loads(metadata.read_text(encoding="utf-8")),
                )
                if (folder / "prepared.wav").is_file():
                    write_json(
                        output / "audio_link.json",
                        {"path": str((folder / "prepared.wav").resolve())},
                    )
                elif (folder / "audio_link.json").is_file():
                    write_json(
                        output / "audio_link.json",
                        json.loads(
                            (folder / "audio_link.json").read_text(encoding="utf-8")
                        ),
                    )
                break
        llm = LocalLLM(settings)
        try:
            analyze(
                transcript,
                llm,
                run,
                settings.role_threshold,
                confirmed,
                settings.chunk_chars,
                pause_for_roles=True,
                profile=profile,
            )
        finally:
            llm.close()
