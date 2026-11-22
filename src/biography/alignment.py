from collections import defaultdict

from .schemas import Role, RoleResult, Segment, Turn


def align(segments: list[Segment], turns: list[Turn]) -> list[Segment]:
    """Conservative ASR/diarization alignment: never assign mixed utterances."""
    result = []
    for original in segments:
        segment = original.model_copy(deep=True)
        scores = defaultdict(int)
        intervals = defaultdict(list)
        for turn in turns:
            start, end = (
                max(turn.start_ms, segment.start_ms),
                min(turn.end_ms, segment.end_ms),
            )
            if end > start:
                intervals[turn.speaker_id].append((start, end))
        # Union intervals so overlapping windows of one speaker do not inflate scores.
        for speaker, spans in intervals.items():
            end = -1
            for a, b in sorted(spans):
                scores[speaker] += max(0, b - max(a, end))
                end = max(end, b)
        ranked = sorted(scores, key=scores.get, reverse=True)
        segment.overlapping_speakers = ranked if len(ranked) > 1 else []
        segment.speaker_id = None
        segment.alignment_confidence = 0
        segment.speaker_role = Role.unknown
        segment.role_confidence = 0
        segment.requires_review = True
        if ranked:
            share = min(1.0, scores[ranked[0]] / (segment.end_ms - segment.start_ms))
            segment.alignment_confidence = share
            if len(ranked) == 1 and share >= 0.8:
                segment.speaker_id = ranked[0]
        result.append(segment)
    return result


def apply_roles(
    segments: list[Segment],
    proposed: RoleResult,
    threshold: float,
    confirmed: dict[str, Role] | None = None,
) -> list[Segment]:
    known = {s.speaker_id for s in segments if s.speaker_id}
    rows = {r.speaker_id: r for r in proposed.speakers}
    if len(rows) != len(proposed.speakers) or set(rows) - known:
        raise ValueError("Некорректные идентификаторы говорящих")
    ids = {s.segment_id: s for s in segments}
    invalid_sources = set()
    for row in rows.values():
        if any(
            i not in ids or ids[i].speaker_id != row.speaker_id
            for i in row.source_segment_ids
        ):
            invalid_sources.add(row.speaker_id)
    confirmed = confirmed or {}
    if set(confirmed) - known:
        raise ValueError("Подтверждение содержит неизвестного говорящего")
    roles = {k: confirmed.get(k, v.role) for k, v in rows.items()}
    roles.update(confirmed)
    disputed = set()
    for role in (Role.respondent, Role.interviewer):
        matching = {k for k, value in roles.items() if value == role}
        if len(matching) > 1:
            if sum(value == role for value in confirmed.values()) > 1:
                raise ValueError(
                    "Подтверждённая роль респондента/интервьюера должна быть уникальной"
                )
            disputed.update(matching - set(confirmed))
    result = []
    for original in segments:
        s = original.model_copy(deep=True)
        row = rows.get(s.speaker_id)
        if s.speaker_id in confirmed:
            s.speaker_role = confirmed[s.speaker_id]
            s.role_confidence = 1
        elif s.speaker_id in disputed or s.speaker_id in invalid_sources:
            s.speaker_role = Role.unknown
            s.role_confidence = 0
        elif row:
            s.speaker_role = row.role
            s.role_confidence = row.confidence
        s.requires_review = (
            s.speaker_id is None
            or s.speaker_role == Role.unknown
            or s.role_confidence < threshold
        )
        result.append(s)
    return result


def coalesce(segments: list[Segment]) -> list[Segment]:
    """Join word timestamps only after speaker alignment; preserve uncertain spans."""
    result = []
    for original in segments:
        s = original.model_copy(deep=True)
        if (
            result
            and s.speaker_id is not None
            and result[-1].speaker_id == s.speaker_id
            and 0 <= s.start_ms - result[-1].end_ms <= 1200
            and len(result[-1].text_raw) + len(s.text_raw) < 600
        ):
            previous = result[-1]
            previous.text_raw += " " + s.text_raw
            previous.end_ms = s.end_ms
            previous.alignment_confidence = min(
                previous.alignment_confidence, s.alignment_confidence
            )
        else:
            result.append(s)
    for i, s in enumerate(result, 1):
        s.segment_id = f"seg_{i:06d}"
    return result
