import socket

import pytest

from biography.intelligence import extract, validate_sources
from biography.offline import offline_mode
from biography.schemas import FactSet, Segment


def samples():
    segments = [
        Segment(
            segment_id="s1",
            start_ms=0,
            end_ms=1000,
            text_raw="Мой отец Иван родился примерно в 1962 году.",
        ),
        Segment(
            segment_id="s2",
            start_ms=1100,
            end_ms=2000,
            text_raw="Нет, отец родился в 1963 году.",
        ),
    ]
    data = FactSet.model_validate(
        dict(
            people=[
                dict(
                    person_id="father",
                    name="Иван",
                    identity_status="explicit",
                    relationship_to_respondent="father",
                    mentions=[dict(segment_id="s1", text="отец Иван")],
                )
            ],
            facts=[
                dict(
                    fact_id="f1",
                    subject_person_id="father",
                    predicate="birth_year",
                    value="1962",
                    date=dict(
                        value="примерно в 1962 году", precision="year", approximate=True
                    ),
                    evidence=[dict(segment_id="s1", quote="примерно в 1962 году")],
                    evidence_type="reported",
                    certainty="approximate",
                ),
                dict(
                    fact_id="f2",
                    subject_person_id="father",
                    predicate="birth_year",
                    value="1963",
                    evidence=[dict(segment_id="s2", quote="отец родился в 1963 году")],
                    evidence_type="reported",
                    certainty="explicit",
                ),
            ],
            conflicts=[
                dict(
                    conflict_group_id="c1",
                    fact_ids=["f1", "f2"],
                    explanation="Разные годы рождения",
                )
            ],
        )
    )
    return segments, data


def test_conflict_keeps_both_versions_and_approximate_date():
    segments, data = samples()
    out = validate_sources(data, segments)
    assert len(out.facts) == 2
    assert all(f.has_conflict and f.requires_review for f in out.facts)
    assert out.facts[0].date.value == "примерно в 1962 году"


@pytest.mark.parametrize("change", ["quote", "source", "person", "conflict", "mention"])
def test_rejects_invented_provenance(change):
    segments, data = samples()
    if change == "quote":
        data.facts[0].evidence[0].quote = "Москва"
    if change == "source":
        data.facts[0].evidence[0].segment_id = "missing"
    if change == "person":
        data.facts[0].subject_person_id = "missing"
    if change == "conflict":
        data.conflicts[0].fact_ids = ["f1", "missing"]
    if change == "mention":
        data.people[0].mentions[0].text = "Пётр"
    with pytest.raises(ValueError):
        validate_sources(data, segments)


def test_unresolved_person_is_not_assigned_to_respondent():
    segments, data = samples()
    data.facts[0].subject_person_id = None
    assert validate_sources(data, segments).facts[0].requires_review
    assert data.facts[0].subject_person_id is None


def test_chunk_extraction_with_injected_backend():
    segments, data = samples()
    calls = []

    def backend(system, payload, schema):
        calls.append(payload)
        return data

    out = extract(segments, backend, 5000)
    assert len(calls) == 1 and len(out.facts) == 2


def test_offline_guard_blocks_socket_and_restores_it():
    original = socket.socket.connect
    with offline_mode(), socket.socket() as s:
        with pytest.raises(RuntimeError, match="Сеть отключена"):
            s.connect(("127.0.0.1", 1))
    assert socket.socket.connect is original


def test_duplicate_and_conflict_candidates_do_not_drop_facts():
    from biography.intelligence import audit_candidates

    _, data = samples()
    duplicate = data.facts[0].model_copy(deep=True)
    duplicate.fact_id = "f3"
    data.facts.append(duplicate)
    report = audit_candidates(data.facts)
    assert report["duplicate_candidates"] == [["f1", "f3"]]
    assert report["conflict_candidates"] == [["f1", "f2", "f3"]]
    assert len(data.facts) == 3
