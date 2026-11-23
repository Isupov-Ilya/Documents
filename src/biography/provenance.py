"""Resolve literal citations spanning adjacent ASR fragments, without inventing text."""

from .schemas import Evidence


def expand_adjacent_evidence(data, segments):
    result = data.model_copy(deep=True)
    ordered = sorted(segments, key=lambda s: (s.start_ms, s.end_ms))
    indices = {s.segment_id: i for i, s in enumerate(ordered)}
    repairs = []
    for fact in result.facts:
        expanded = []
        for evidence in fact.evidence:
            index = indices.get(evidence.segment_id)
            if index is None or evidence.quote in ordered[index].text_raw:
                expanded.append(evidence)
                continue
            found = None
            for start in range(max(0, index - 2), index + 1):
                for end in range(index + 1, min(len(ordered), index + 3) + 1):
                    window = ordered[start:end]
                    if len(window) < 2:
                        continue
                    # A citation may span clipped boundary words, never distant dialogue.
                    if any(
                        b.start_ms - a.end_ms > 350 or b.start_ms < a.start_ms
                        for a, b in zip(window, window[1:])
                    ):
                        continue
                    speakers = {
                        s.speaker_id for s in window if s.speaker_id is not None
                    }
                    if len(speakers) > 1:
                        continue
                    text = " ".join(s.text_raw for s in window)
                    offset = text.find(evidence.quote)
                    if offset < 0:
                        continue
                    cursor = 0
                    pieces = []
                    for segment in window:
                        left = max(offset, cursor)
                        right = min(
                            offset + len(evidence.quote), cursor + len(segment.text_raw)
                        )
                        if right > left:
                            pieces.append(
                                Evidence(
                                    segment_id=segment.segment_id,
                                    quote=segment.text_raw[
                                        left - cursor : right - cursor
                                    ],
                                )
                            )
                        cursor += len(segment.text_raw) + 1
                    if not any(p.segment_id == evidence.segment_id for p in pieces):
                        continue
                    if len(pieces) < 2:
                        continue
                    found = pieces
                    break
                if found:
                    break
            if found:
                repairs.append(
                    {
                        "fact_id": fact.fact_id,
                        "original": evidence.model_dump(),
                        "expanded": [p.model_dump() for p in found],
                    }
                )
                expanded.extend(found)
                fact.requires_review = True
            else:
                expanded.append(evidence)
        fact.evidence = expanded
    return result, repairs


def semantic_warnings(data):
    """Conservative inconsistency hints. Do not change the reported meaning."""
    warnings = []
    for fact in data.facts:
        text = " ".join(e.quote for e in fact.evidence).casefold()
        predicate = fact.predicate.casefold()
        problem = None
        if predicate in {"birth_year", "date_of_birth", "place_of_birth"}:
            if not any(s in text for s in ("родил", "рожд")):
                problem = "В цитате нет явного сообщения о рождении; вид события требует проверки."
        if (
            "relocat" in predicate
            and "увол" in text
            and not any(s in text for s in ("переех", "переезд"))
        ):
            problem = "Цитата говорит об увольнении, а тип факта — о переезде."
        if problem:
            fact.requires_review = True
            warnings.append({"fact_id": fact.fact_id, "reason": problem})
    return warnings
