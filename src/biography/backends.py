import gc
import json
from pathlib import Path

from .errors import ContextBudgetError
from .schemas import Segment, Turn


def local_path(value: str, directory=False) -> Path:
    path = Path(value).expanduser().resolve(strict=True)
    if (directory and not path.is_dir()) or (not directory and not path.is_file()):
        raise ValueError(f"Неверный тип локальной модели: {path}")
    return path


def _transcribe_worker(wav: Path, settings) -> list[Segment]:
    from faster_whisper import WhisperModel

    path = local_path(settings.whisper_model, directory=True)
    model = WhisperModel(
        str(path),
        device=settings.device,
        compute_type=settings.compute_type,
        cpu_threads=settings.threads,
        local_files_only=True,
    )
    try:
        iterator, _ = model.transcribe(
            str(wav),
            language="ru",
            beam_size=5,
            vad_filter=True,
            word_timestamps=True,
            condition_on_previous_text=False,
        )
        result = []
        for item in iterator:
            words = list(item.words or [])
            # Word-level pieces avoid assigning one speaker to a long ASR sentence.
            pieces = [(w.start, w.end, w.word) for w in words] or [
                (item.start, item.end, item.text)
            ]
            for start, end, text in pieces:
                if not text.strip() or end <= start:
                    continue
                text = text.strip()
                if item.avg_logprob < -1.0 or item.no_speech_prob > 0.6:
                    text = "[неразборчиво]"
                result.append(
                    Segment(
                        segment_id=f"seg_{len(result) + 1:06d}",
                        start_ms=round(start * 1000),
                        end_ms=max(round(end * 1000), round(start * 1000) + 1),
                        text_raw=text,
                    )
                )
        if not result:
            raise ValueError("Речь не распознана; факты не будут созданы")
        return result
    finally:
        del model
        gc.collect()


def diarize(wav: Path, settings) -> list[Turn]:
    if settings.diarization_backend == "sherpa_onnx":
        from .sherpa_backend import diarize_sherpa

        return diarize_sherpa(wav, settings)
    import torch
    from pyannote.audio import Pipeline

    config = local_path(settings.diarization_config)
    # Local config must reference local segmentation/embedding checkpoints.
    # Offline guard prevents remote fallback if the config is incomplete.
    pipeline = Pipeline.from_pretrained(str(config))
    if pipeline is None:
        raise ValueError("Не удалось открыть локальную модель диаризации")
    pipeline.to(torch.device(settings.device))
    try:
        annotation = pipeline(
            str(wav),
            min_speakers=settings.min_speakers,
            max_speakers=settings.max_speakers,
        )
        return [
            Turn(
                start_ms=max(0, round(part.start * 1000)),
                end_ms=round(part.end * 1000),
                speaker_id=str(label),
            )
            for part, _, label in annotation.itertracks(yield_label=True)
            if round(part.end * 1000) > max(0, round(part.start * 1000))
        ]
    finally:
        del pipeline
        gc.collect()
        if settings.device == "cuda":
            torch.cuda.empty_cache()


class LocalLLM:
    def __init__(self, settings):
        from llama_cpp import Llama

        path = local_path(settings.llm_model)
        self.context = settings.llm_context
        self.sampling = {
            "temperature": settings.llm_temperature,
            "top_p": settings.llm_top_p,
            "top_k": settings.llm_top_k,
            "min_p": settings.llm_min_p,
        }
        self.model = Llama(
            model_path=str(path),
            n_ctx=self.context,
            n_batch=128,
            n_threads=settings.threads,
            n_gpu_layers=settings.llm_gpu_layers,
            verbose=False,
            seed=42,
        )

    def __call__(self, system: str, payload: dict, schema):
        content = json.dumps(payload, ensure_ascii=False)
        response_schema = schema.model_json_schema()
        if schema.__name__ == "FactSet":
            response_schema.get("$defs", {}).get("Fact", {}).get("properties", {}).pop(
                "human_reviewed", None
            )
        # Grammar restricts syntax, but does not explain field meanings to the LLM.
        system += (
            "\nФормат результата — JSON по следующей схеме. "
            "Верни только объект JSON, без пояснений вне него:\n"
            + json.dumps(response_schema, ensure_ascii=False, separators=(",", ":"))
        )
        # Reserve space for structured output and chat-template overhead.
        tokens = len(self.model.tokenize((system + content).encode("utf-8")))
        budget = self.context - tokens - 512
        if budget < 1024:
            raise ContextBudgetError(
                "Недостаточно контекста LLM. Уменьшите chunk_chars или увеличьте llm_context"
            )
        response = self.model.create_chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            response_format={
                "type": "json_object",
                "schema": response_schema,
            },
            **self.sampling,
            max_tokens=budget,
        )
        choice = response["choices"][0]
        if choice["finish_reason"] != "stop":
            raise ContextBudgetError(
                "Ответ LLM обрезан; увеличьте контекст или уменьшите chunk_chars"
            )
        return schema.model_validate_json(choice["message"]["content"])

    def close(self):
        self.model.close()


def transcribe(wav: Path, settings) -> list[Segment]:
    import subprocess

    from .runtime import process_command

    local_path(settings.whisper_model, directory=True)
    response = subprocess.run(
        process_command("asr", str(wav.resolve())),
        input=settings.model_dump_json(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if response.returncode:
        raise RuntimeError(
            f"ASR worker exited {response.returncode}: {response.stderr[-3000:]}"
        )
    return [Segment.model_validate(x) for x in json.loads(response.stdout)]
