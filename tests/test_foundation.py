import shutil
import wave

import pytest
from pydantic import ValidationError

from biography.alignment import align, apply_roles
from biography.audio import AudioError, prepare
from biography.schemas import Role, RoleResult, Segment, Transcript, Turn


def segment(**kw):
    return Segment(
        segment_id="s1", start_ms=0, end_ms=1000, text_raw="Я родился в Самаре.", **kw
    )


def test_invalid_intervals_and_ids():
    with pytest.raises(ValidationError):
        Segment(segment_id="a", start_ms=10, end_ms=5, text_raw="x")
    with pytest.raises(ValidationError):
        Transcript(
            source_sha256="x", duration_seconds=2, segments=[segment(), segment()]
        )


def test_alignment_refuses_mixed_and_missing_speech():
    s = segment()
    one = Turn(start_ms=0, end_ms=1000, speaker_id="a")
    assert align([s], [one])[0].speaker_id == "a"
    mixed = align([s], [one, Turn(start_ms=500, end_ms=1000, speaker_id="b")])[0]
    assert mixed.speaker_id is None and mixed.requires_review
    assert align([s], [])[0].speaker_id is None
    assert s.speaker_id is None


def test_roles_need_review_until_confirmed():
    s = segment(speaker_id="a")
    roles = RoleResult.model_validate(
        {
            "speakers": [
                dict(
                    speaker_id="a",
                    role="respondent",
                    confidence=0.5,
                    source_segment_ids=["s1"],
                    reason="Контекст",
                )
            ]
        }
    )
    assert apply_roles([s], roles, 0.85)[0].requires_review
    assert not apply_roles([s], roles, 0.85, {"a": Role.respondent})[0].requires_review
    with pytest.raises(ValueError):
        apply_roles([s], roles, 0.85, {"b": Role.interviewer})


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_decode_preserves_source_and_rejects_corruption(tmp_path):
    source = tmp_path / "voice.wav"
    with wave.open(str(source), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(8000)
        f.writeframes(b"\x00" * 32000)
    original = source.read_bytes()
    info = prepare(source, tmp_path / "prepared.wav")
    assert info["duration_seconds"] == 1
    assert source.read_bytes() == original
    with wave.open(str(tmp_path / "prepared.wav")) as f:
        assert f.getnchannels() == 1 and f.getframerate() == 16000
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not audio")
    with pytest.raises(AudioError):
        prepare(bad, tmp_path / "bad.wav")
    with pytest.raises(AudioError):
        prepare(source, source)


def test_conflicting_model_roles_require_review_instead_of_assignment():
    from biography.schemas import SpeakerRole

    a = segment(speaker_id="a")
    b = Segment(
        segment_id="s2", start_ms=1000, end_ms=2000, text_raw="Вопрос?", speaker_id="b"
    )
    rows = RoleResult(
        speakers=[
            SpeakerRole(
                speaker_id=x,
                role=Role.interviewer,
                confidence=0.99,
                source_segment_ids=[sid],
                reason="Модель ошиблась",
            )
            for x, sid in [("a", "s1"), ("b", "s2")]
        ]
    )
    result = apply_roles([a, b], rows, 0.85)
    assert all(s.speaker_role == Role.unknown and s.requires_review for s in result)
    result = apply_roles([a, b], rows, 0.85, {"a": Role.interviewer})
    assert result[0].speaker_role == Role.interviewer and not result[0].requires_review
    assert result[1].speaker_role == Role.unknown
    with pytest.raises(ValueError, match="Подтверждённая"):
        apply_roles([a, b], rows, 0.85, {"a": Role.respondent, "b": Role.respondent})


def test_wrong_role_citations_become_unknown_not_trusted():
    from biography.schemas import SpeakerRole

    row = SpeakerRole(
        speaker_id="a",
        role=Role.respondent,
        confidence=1,
        source_segment_ids=["missing"],
        reason="Unsupported reference",
    )
    out = apply_roles([segment(speaker_id="a")], RoleResult(speakers=[row]), 0.85)[0]
    assert out.speaker_role == Role.unknown and out.requires_review
    confirmed = apply_roles(
        [segment(speaker_id="a")],
        RoleResult(speakers=[row]),
        0.85,
        {"a": Role.respondent},
    )[0]
    assert confirmed.speaker_role == Role.respondent and not confirmed.requires_review
