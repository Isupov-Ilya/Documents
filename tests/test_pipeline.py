import json
import shutil
import wave
from pathlib import Path

import pytest

from biography.alignment import coalesce
from biography.cli import main
from biography.demo import run_demo
from biography.pipeline import Run, run_audio
from biography.schemas import Role, Segment, Settings, Turn

FIXTURES = Path(__file__).resolve().parents[1] / "examples"


def test_demo_end_to_end_and_no_overwrite(tmp_path):
    out = tmp_path / "result"
    run_demo(FIXTURES, out)
    report = json.loads((out / "quality_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "completed" and report["accuracy_measured"] is False
    assert report["mode"] == "synthetic_fixture_no_models"
    assert (report["people"], report["facts"], report["conflicts"]) == (3, 8, 1)
    data = json.loads((out / "facts.json").read_text(encoding="utf-8"))
    assert data["facts"][-1]["subject_person_id"] is None
    assert data["people"][1]["person_id"] != data["people"][2]["person_id"]
    before = (out / "facts.json").read_bytes()
    assert main(["demo", "--fixtures", str(FIXTURES), "--output", str(out)]) == 1
    assert (out / "facts.json").read_bytes() == before


def test_stage_failure_and_cancellation_saved(tmp_path):
    for error, status in [
        (ValueError("model absent"), "failed"),
        (KeyboardInterrupt(), "cancelled"),
    ]:
        out = tmp_path / status
        with pytest.raises(type(error)), Run(out, "test") as run:
            with run.stage("transcription"):
                raise error
        report = json.loads((out / "quality_report.json").read_text(encoding="utf-8"))
        assert (
            report["status"] == status
            and report["stages"]["transcription"]["status"] == status
        )
        assert "model absent" in report["error"] if status == "failed" else True


def test_coalesce_respects_gaps_and_speakers():
    def s(i, start, end, text, speaker):
        return Segment(
            segment_id=str(i),
            start_ms=start,
            end_ms=end,
            text_raw=text,
            speaker_id=speaker,
        )

    words = [
        s(1, 0, 100, "Мой", "a"),
        s(2, 100, 300, "отец", "a"),
        s(3, 300, 400, "Да", "b"),
        s(4, 400, 500, "? ", None),
    ]
    out = coalesce(words)
    assert [s.text_raw for s in out] == ["Мой отец", "Да", "? "]
    assert len({s.segment_id for s in out}) == 3


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_real_audio_pipeline_with_explicit_test_adapters(tmp_path, monkeypatch):
    # FFmpeg really runs; absent neural models are replaced ONLY in this test.
    from biography import backends

    source = tmp_path / "input.wav"
    with wave.open(str(source), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x00" * 3200000)
    fixture = json.loads((FIXTURES / "transcript.json").read_text(encoding="utf-8"))
    segments = [Segment.model_validate(s) for s in fixture["segments"]]
    # Keep scripted source IDs stable: these are full utterances separated by pauses.
    for s in segments:
        s.end_ms -= 1000
    monkeypatch.setattr(
        backends,
        "diarize",
        lambda *args: [
            Turn(start_ms=s.start_ms, end_ms=s.end_ms, speaker_id=s.speaker_id)
            for s in segments
            if s.speaker_id
        ],
    )
    monkeypatch.setattr(backends, "transcribe", lambda *args: segments)
    from biography.schemas import RoleResult

    class Scripted:
        def __init__(self, *args):
            pass

        def __call__(self, system, payload, schema):
            file = "roles.json" if schema is RoleResult else "facts_expected.json"
            return schema.model_validate_json(
                (FIXTURES / file).read_text(encoding="utf-8")
            )

        def close(self):
            pass

    monkeypatch.setattr(backends, "LocalLLM", Scripted)
    cfg = Settings(
        whisper_model="absent",
        diarization_config="absent",
        llm_model="absent",
        chunk_chars=20000,
    )
    original = source.read_bytes()
    out = tmp_path / "output"
    from biography.pipeline import NeedsRoleConfirmation

    with pytest.raises(NeedsRoleConfirmation):
        run_audio(source, cfg, out)
    paused = json.loads((out / "quality_report.json").read_text(encoding="utf-8"))
    assert paused["status"] == "awaiting_roles"
    assert not (out / "facts.json").exists()
    out = tmp_path / "confirmed-output"
    run_audio(
        source,
        cfg,
        out,
        {
            "SPEAKER_00": Role.interviewer,
            "SPEAKER_01": Role.respondent,
            "SPEAKER_02": Role.third_party,
        },
    )
    assert (
        json.loads((out / "quality_report.json").read_text(encoding="utf-8"))["status"]
        == "completed"
    )
    assert source.read_bytes() == original
    assert (
        len(json.loads((out / "facts.json").read_text(encoding="utf-8"))["facts"]) == 8
    )


def test_exported_schemas_match_current_models(tmp_path):
    from biography.schemas import FactSet, RoleResult, Transcript

    out = tmp_path / "schemas"
    assert main(["schemas", "--output", str(out)]) == 0
    for model in [Transcript, FactSet, RoleResult]:
        assert (
            json.loads(
                (out / f"{model.__name__}.schema.json").read_text(encoding="utf-8")
            )
            == model.model_json_schema()
        )


def test_check_missing_models_is_actionable_without_importing_them(tmp_path, capsys):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            dict(
                whisper_model="missing",
                diarization_config="missing.yaml",
                llm_model="missing.gguf",
            )
        )
    )
    assert main(["check", "--config", str(config)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "prerequisites_missing"
    assert any("model.bin" in message for message in result["errors"])
