"""Resolve literal citations spanning adjacent ASR fragments, without inventing text."""

from collections import Counter

from .schemas import Evidence


def ground_model_part(part, known, segments):
    """Quarantine ungrounded model candidates; saved/user data still validates strictly."""
    result, repairs = expand_adjacent_evidence(part, segments)
    lookup = {s.segment_id: s.text_raw for s in segments}
    rejected = []

    def literal(source, quote):
        return (
            source in lookup
            and bool(quote.strip())
            and quote in lookup[source]
            and "[неразборчиво]" not in quote.casefold()
        )

    def reject(kind, identifier, reason, sources):
        rejected.append(
            {
                "kind": kind,
                "candidate_id": identifier,
                "reason": reason,
                "source_segment_ids": sorted(set(sources)),
            }
        )
        result.uncertainties.append(
            f"Отклонено неподтверждённое предложение модели ({kind}, {identifier}): "
            + reason
            + ". Проверьте исходный текст; исходный ответ сохранён в facts_batch."
        )

    people = []
    ambiguous_people = {
        key
        for key, count in Counter(p.person_id for p in result.people).items()
        if count > 1
    }
    for person in result.people:
        if person.person_id in ambiguous_people:
            reject("лицо", person.person_id, "повторяющийся неоднозначный ID", [])
            continue
        valid = [m for m in person.mentions if literal(m.segment_id, m.text)]
        if len(valid) != len(person.mentions):
            reject(
                "лицо",
                person.person_id,
                "упоминание не совпало с транскрипцией",
                [m.segment_id for m in person.mentions],
            )
        if valid:
            person.mentions = valid
            people.append(person)
    result.people = people
    person_ids = {p.person_id for p in known.people + people}

    facts = []
    ambiguous_facts = {
        key
        for key, count in Counter(f.fact_id for f in result.facts).items()
        if count > 1
    }
    for fact in result.facts:
        if fact.fact_id in ambiguous_facts:
            reject("сведение", fact.fact_id, "повторяющийся неоднозначный ID", [])
            continue
        if (
            fact.subject_person_id is not None
            and (
                fact.subject_person_id not in person_ids
                or fact.subject_person_id in ambiguous_people
            )
        ) or not all(literal(e.segment_id, e.quote) for e in fact.evidence):
            reject(
                "сведение",
                fact.fact_id,
                "нет подтверждённого лица или точной цитаты",
                [e.segment_id for e in fact.evidence],
            )
            continue
        # Never retain a multi-source claim after removing only its bad citation.
        fact.human_reviewed = False
        fact.requires_review = True
        facts.append(fact)
    result.facts = facts

    relations = []
    for index, relation in enumerate(result.relations, 1):
        if (
            relation.subject_person_id not in person_ids
            or relation.object_person_id not in person_ids
            or relation.subject_person_id in ambiguous_people
            or relation.object_person_id in ambiguous_people
            or not all(literal(e.segment_id, e.quote) for e in relation.evidence)
        ):
            reject(
                "связь",
                str(index),
                "нет подтверждённого лица или точной цитаты",
                [e.segment_id for e in relation.evidence],
            )
            continue
        relation.requires_review = True
        relations.append(relation)
    result.relations = relations

    fact_ids = {f.fact_id for f in known.facts + facts} - ambiguous_facts
    conflicts = []
    for conflict in result.conflicts:
        ids = list(dict.fromkeys(i for i in conflict.fact_ids if i in fact_ids))
        if len(ids) != len(set(conflict.fact_ids)) or len(ids) < 2:
            reject(
                "противоречие",
                conflict.conflict_group_id,
                "часть утверждений не подтверждена",
                [],
            )
        if len(ids) >= 2:
            conflict.fact_ids = ids
            conflicts.append(conflict)
    result.conflicts = conflicts
    grouped = {i: c.conflict_group_id for c in conflicts for i in c.fact_ids}
    for fact in facts:
        if (
            fact.has_conflict or fact.conflict_group_id
        ) and fact.fact_id not in grouped:
            reject(
                "противоречие",
                fact.fact_id,
                "нет подтверждённой группы противоречий",
                [e.segment_id for e in fact.evidence],
            )
        fact.has_conflict = fact.fact_id in grouped
        fact.conflict_group_id = grouped.get(fact.fact_id)
    for repair in repairs:
        result.uncertainties.append(
            "Цитата расширена на соседние фрагменты: " + repair["fact_id"]
        )
    return result, rejected


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
