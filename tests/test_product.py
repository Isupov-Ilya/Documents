import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from docx import Document
from docx.shared import Pt

from biography.assets import download_one
from biography.documents import export_documents
from biography.intelligence import validate_sources
from biography.provenance import expand_adjacent_evidence, semantic_warnings
from biography.schemas import FactSet, Segment, Transcript
from biography.templates import analyze_samples, create_profile

FIXTURES = Path(__file__).resolve().parents[1] / "examples"


def cited_fact():
    return FactSet.model_validate(
        {
            "people": [
                {
                    "person_id": "colleague",
                    "name": "Иван",
                    "identity_status": "explicit",
                    "mentions": [{"segment_id": "s2", "text": "Иван"}],
                }
            ],
            "facts": [
                {
                    "fact_id": "f1",
                    "subject_person_id": "colleague",
                    "predicate": "relocation",
                    "value": "Москва",
                    "evidence": [
                        {
                            "segment_id": "s2",
                            "quote": "Мой коллега Иван переехал в Москву",
                        }
                    ],
                    "evidence_type": "reported",
                    "certainty": "explicit",
                }
            ],
        }
    )


def split_segments(gap=0, speaker=None):
    return [
        Segment(
            segment_id="s1", start_ms=0, end_ms=300, text_raw="Мой", speaker_id=speaker
        ),
        Segment(
            segment_id="s2",
            start_ms=300 + gap,
            end_ms=3000 + gap,
            text_raw="коллега Иван переехал в Москву.",
            speaker_id="a",
        ),
    ]


def test_literal_citation_expansion_preserves_every_source():
    original = cited_fact()
    data, repairs = expand_adjacent_evidence(original, split_segments())
    validate_sources(data, split_segments())
    assert [e.segment_id for e in data.facts[0].evidence] == ["s1", "s2"]
    assert repairs and data.facts[0].requires_review
    assert original.facts[0].evidence[0].quote == "Мой коллега Иван переехал в Москву"


@pytest.mark.parametrize("gap,speaker", [(4000, None), (0, "other")])
def test_distant_or_other_speaker_citation_cannot_be_repaired(gap, speaker):
    segments = split_segments(gap, speaker)
    data, repairs = expand_adjacent_evidence(cited_fact(), segments)
    assert not repairs
    with pytest.raises(ValueError):
        validate_sources(data, segments)


def test_invented_quote_still_fails_validation():
    data = cited_fact()
    data.facts[0].evidence[0].quote = "Мой коллега Иван родился в 1980 году"
    result, repairs = expand_adjacent_evidence(data, split_segments())
    with pytest.raises(ValueError):
        validate_sources(result, split_segments())


def test_download_resume_checks_range_and_complete_sha256(tmp_path, monkeypatch):
    content = b"full trusted weights"
    record = {
        "relative_path": "model.bin",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "repo": "owner/repo",
        "revision": "abc",
        "file": "model.bin",
    }
    (tmp_path / "model.bin.partial").write_bytes(content[:5])

    class Response(io.BytesIO):
        status = 206
        headers = {"Content-Range": f"bytes 5-{len(content) - 1}/{len(content)}"}

    def respond(request, **kwargs):
        assert request.headers["Range"] == "bytes=5-"
        return Response(content[5:])

    monkeypatch.setattr("biography.assets.urllib.request.urlopen", respond)
    output = download_one(record, tmp_path)
    assert output.read_bytes() == content
    assert not (tmp_path / "model.bin.partial").exists()


def test_wrong_download_does_not_replace_good_existing_file(tmp_path, monkeypatch):
    record = {
        "relative_path": "model.bin",
        "size": 4,
        "sha256": hashlib.sha256(b"good").hexdigest(),
        "repo": "a/b",
        "revision": "abc",
        "file": "model.bin",
    }
    (tmp_path / "model.bin").write_bytes(b"previous file")

    class Response(io.BytesIO):
        status = 200
        headers = {}

    monkeypatch.setattr(
        "biography.assets.urllib.request.urlopen", lambda *a, **kw: Response(b"evil")
    )
    with pytest.raises(ValueError, match="Контрольная сумма"):
        download_one(record, tmp_path)
    assert (tmp_path / "model.bin").read_bytes() == b"previous file"


def test_exports_have_all_three_documents_and_review_notice(tmp_path):
    transcript = Transcript.model_validate_json(
        (FIXTURES / "transcript.json").read_text(encoding="utf-8")
    )
    facts = FactSet.model_validate_json(
        (FIXTURES / "facts_expected.json").read_text(encoding="utf-8")
    )
    output = export_documents(transcript, facts, tmp_path)
    assert set(output) == {"biography", "verbatim", "clean"}
    d = Document(output["biography"])
    text = "\n".join(p.text for p in d.paragraphs)
    assert "требует проверки человеком" in text and "ПРОТИВОРЕЧИЕ" in text
    assert "примерно в 1962" not in text
    assert "1950" in text and "1951" in text
    assert "Сведения не представлены" in text
    assert (tmp_path / "document_validation.json").is_file()


def test_template_preserves_table_and_format_without_source_personal_text(tmp_path):
    source = tmp_path / "sample.docx"
    d = Document()
    d.core_properties.author = "PRIVATE_AUTHOR"
    p = d.add_paragraph()
    run = p.add_run("СЕКРЕТНОЕ ИМЯ ИЗ ОБРАЗЦА")
    run.bold = True
    run.font.size = Pt(16)
    table = d.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "ЧУЖАЯ ДАТА"
    table.cell(1, 1).text = "Постоянная безопасная фраза"
    d.save(source)
    blocks = analyze_samples([source])
    choices = [{"field": "full_name", "fixed": False} for b in blocks]
    choices[-1] = {"field": "missing", "fixed": True, "fixed_confirmed": True}
    profile = create_profile([source], choices, tmp_path / "profile", "Тест")
    from biography.templates import render_profile

    result, _ = render_profile(profile, {"full_name": "Анна Новая"})
    out = tmp_path / "result.docx"
    result.save(out)
    assert len(result.tables) == 1 and len(result.tables[0].rows) == 2
    assert result.paragraphs[0].runs[0].bold is True
    assert result.paragraphs[0].runs[0].font.size == Pt(16)
    with zipfile.ZipFile(out) as z:
        xml = "\n".join(
            z.read(n).decode("utf-8", errors="replace")
            for n in z.namelist()
            if n.endswith(".xml")
        )
    assert (
        "СЕКРЕТНОЕ" not in xml
        and "ЧУЖАЯ ДАТА" not in xml
        and "PRIVATE_AUTHOR" not in xml
    )
    assert "Анна Новая" in xml and "Постоянная безопасная фраза" in xml


def test_fixed_template_text_requires_explicit_confirmation(tmp_path):
    source = tmp_path / "sample.docx"
    d = Document()
    d.add_paragraph("Текст")
    d.save(source)
    with pytest.raises(ValueError, match="подтверждён"):
        create_profile(
            [source],
            [{"fixed": True, "fixed_confirmed": False}],
            tmp_path / "p",
            "Тест",
        )


def test_semantic_event_mismatch_is_flagged_and_not_rendered_as_a_claim(tmp_path):
    data = cited_fact()
    data.facts[0].predicate = "date_of_relocation"
    data.facts[0].value = "2010"
    data.facts[0].evidence[0].quote = "Он уволился в 2010 году."
    segments = split_segments()
    segments[1].text_raw = "коллега Иван. Он уволился в 2010 году."
    assert semantic_warnings(data)[0]["fact_id"] == "f1"
    transcript = Transcript(
        source_sha256="test", duration_seconds=10, segments=segments
    )
    outputs = export_documents(transcript, data, tmp_path)
    text = "\n".join(p.text for p in Document(outputs["biography"]).paragraphs)
    assert "СОМНИТЕЛЬНО" in text and "Он уволился в 2010 году" in text


def test_many_chunks_keep_ids_unique_and_do_not_expand_context_without_bound():
    from biography.intelligence import extract

    segments = [
        Segment(
            segment_id=f"s{i}",
            start_ms=i * 1000,
            end_ms=i * 1000 + 900,
            text_raw=f"Участник {i} сообщил о событии {i}.",
        )
        for i in range(80)
    ]
    sizes = []

    def backend(prompt, payload, schema):
        sizes.append(len(json.dumps(payload, ensure_ascii=False)))
        s = payload["segments"][0]
        i = int(s["segment_id"][1:])
        return FactSet.model_validate(
            {
                "people": [
                    {
                        "person_id": "p",
                        "name": f"Участник {i}",
                        "identity_status": "explicit",
                        "mentions": [
                            {"segment_id": s["segment_id"], "text": f"Участник {i}"}
                        ],
                    }
                ],
                "facts": [
                    {
                        "fact_id": "f",
                        "subject_person_id": "p",
                        "predicate": "event",
                        "value": f"событие {i}",
                        "human_reviewed": True,
                        "evidence": [
                            {"segment_id": s["segment_id"], "quote": s["text_raw"]}
                        ],
                        "evidence_type": "reported",
                        "certainty": "explicit",
                    }
                ],
            }
        )

    result = extract(segments, backend, limit=1)
    assert len(result.people) == 80 and len(result.facts) == 80
    assert len({f.fact_id for f in result.facts}) == 80
    assert len({f.subject_person_id for f in result.facts}) == 80
    assert all(not f.human_reviewed for f in result.facts)
    assert max(sizes) < 20000


def test_conflicting_birth_years_are_not_silently_replaced_by_a_range():
    from biography.claims import preserve_competing_claims

    data = cited_fact()
    f = data.facts[0]
    f.predicate = "birth_year"
    f.value = "1950-1951"
    f.certainty = "approximate"
    from biography.schemas import Evidence

    f.evidence = [
        Evidence(segment_id="s1", quote="Отец родился примерно в 1950 году."),
        Evidence(segment_id="s2", quote="Возможно, отец родился в 1951 году."),
    ]
    result = preserve_competing_claims(data)
    assert {f.value for f in result.facts} == {"1950", "1951"}
    assert len(result.conflicts) == 1
    assert all(
        f.has_conflict and f.requires_review and f.date.approximate
        for f in result.facts
    )
    assert data.facts[0].value == "1950-1951"


def test_checkpoint_reuse_rejects_changed_or_moved_artifacts(tmp_path):
    from biography.checkpoints import Checkpoints

    previous = tmp_path / "old"
    previous.mkdir()
    (previous / "stage.json").write_text("original", encoding="utf-8")
    original = Checkpoints(previous)
    original.record("stage", "key", ["stage.json"])
    new = tmp_path / "new"
    new.mkdir()
    reuse = Checkpoints(new, previous)
    assert reuse.restore("stage", "key", ["stage.json"])
    assert (new / "stage.json").read_text(encoding="utf-8") == "original"
    (previous / "stage.json").write_text("modified", encoding="utf-8")
    bad = tmp_path / "bad"
    bad.mkdir()
    check = Checkpoints(bad, previous)
    assert not check.restore("stage", "key", ["stage.json"])
    assert not check.restore("stage", "different-input", ["stage.json"])
    assert not (bad / "stage.json").exists()


def test_history_search_handles_russian_case_without_sql_interpolation(tmp_path):
    from biography.store import JobStore

    store = JobStore(tmp_path)
    store.add("one", "sample.wav", "job")
    store.update("job", "completed", "Иван Волков")
    assert len(store.list("иван")) == 1
    assert store.list("%' OR 1=1 --") == []
    store.close()


def test_context_limit_splits_work_instead_of_losing_segments():
    from biography.errors import ContextBudgetError
    from biography.intelligence import extract

    sources = [
        Segment(
            segment_id=f"s{i}",
            start_ms=i * 1000,
            end_ms=i * 1000 + 900,
            text_raw=f"Событие номер {i}.",
        )
        for i in range(4)
    ]
    processed = []

    def backend(prompt, payload, schema):
        if len(payload["segments"]) > 1:
            raise ContextBudgetError("smaller required")
        s = payload["segments"][0]
        processed.append(s["segment_id"])
        return FactSet(people=[], facts=[])

    result = extract(sources, backend, limit=100000)
    assert processed == ["s0", "s1", "s2", "s3"]
    assert result.facts == []


def test_hour_sized_role_input_is_bounded_without_dropping_a_speaker():
    from biography.intelligence import infer_roles
    from biography.schemas import RoleResult

    sources = [
        Segment(
            segment_id=f"s{i}",
            start_ms=i * 1000,
            end_ms=i * 1000 + 900,
            speaker_id=f"speaker_{i % 3}",
            text_raw="Текст ответа. " * 45,
        )
        for i in range(1800)
    ]

    def backend(prompt, payload, schema):
        assert len(json.dumps(payload, ensure_ascii=False)) < 20000
        assert {s["speaker_id"] for s in payload["segments"]} == {
            "speaker_0",
            "speaker_1",
            "speaker_2",
        }
        return RoleResult(speakers=[])

    assert infer_roles(sources, backend).speakers == []


def test_template_retains_inherited_font_and_table_style_without_private_style_names(
    tmp_path,
):
    from docx.enum.style import WD_STYLE_TYPE

    from biography.templates import render_profile

    source = tmp_path / "sample.docx"
    d = Document()
    style = d.styles.add_style("PRIVATE_STYLE_NAME", WD_STYLE_TYPE.PARAGRAPH)
    style.font.name = "Times New Roman"
    style.font.size = Pt(19)
    style.font.bold = True
    d.add_paragraph("PRIVATE_PERSON_TEXT", style=style)
    t = d.add_table(rows=1, cols=1)
    t.style = "Table Grid"
    t.cell(0, 0).text = "PRIVATE_PERSON_TEXT"
    d.save(source)
    choices = [
        {"field": "full_name", "fixed": False} for _ in analyze_samples([source])
    ]
    profile = create_profile(
        [source], choices, tmp_path / "profile", "PRIVATE_PROFILE_TITLE"
    )
    result, _ = render_profile(
        profile, {"full_name": "Новое имя", "title": "Биография"}
    )
    assert result.paragraphs[0].runs[0].font.size == Pt(19)
    assert result.paragraphs[0].runs[0].bold
    assert result.tables[0].style.style_id == "TableGrid"
    target = tmp_path / "result.docx"
    result.save(target)
    with zipfile.ZipFile(target) as z:
        xml = "\n".join(
            z.read(n).decode("utf-8", errors="replace")
            for n in z.namelist()
            if n.endswith(".xml")
        )
    for private in [
        "PRIVATE_STYLE_NAME",
        "PRIVATE_PERSON_TEXT",
        "PRIVATE_PROFILE_TITLE",
    ]:
        assert private not in xml


def test_unfounded_new_mention_does_not_replace_known_identity_or_pass_as_a_new_person():
    from biography.intelligence import _merge_part

    base = cited_fact()
    base.facts[0].evidence[0].quote = "коллега Иван переехал в Москву"
    known = _merge_part(FactSet(people=[], facts=[]), base, "base", split_segments())
    person = known.people[0]
    update = FactSet.model_validate(
        {
            "people": [
                {
                    "person_id": person.person_id,
                    "name": "Чужое имя",
                    "identity_status": "explicit",
                    "mentions": [
                        {"segment_id": "s1", "text": "Несуществующее упоминание"}
                    ],
                }
            ],
            "facts": [],
        }
    )
    result = _merge_part(known, update, "update", split_segments())
    assert result.people[0].name == "Иван"
    assert result.people[0].mentions == known.people[0].mentions
    assert any("отклонено" in text for text in result.uncertainties)
    update.people[0].person_id = "new-person"
    with pytest.raises(ValueError, match="Упоминание человека"):
        _merge_part(known, update, "invalid", split_segments())


def test_first_mentioned_parent_name_does_not_become_the_respondent():
    from biography.documents import build_sections
    from biography.schemas import Role

    segments = [
        Segment(
            segment_id="father",
            start_ms=0,
            end_ms=1000,
            text_raw="Мой отец Пётр.",
            speaker_role=Role.respondent,
        ),
        Segment(
            segment_id="self",
            start_ms=1000,
            end_ms=2000,
            text_raw="Меня зовут Иван.",
            speaker_role=Role.respondent,
        ),
    ]
    data = FactSet.model_validate(
        {
            "people": [
                {
                    "person_id": "father",
                    "name": "Пётр",
                    "identity_status": "explicit",
                    "mentions": [{"segment_id": "father", "text": "Пётр"}],
                },
                {
                    "person_id": "self",
                    "name": "Иван",
                    "identity_status": "explicit",
                    "mentions": [{"segment_id": "self", "text": "Иван"}],
                },
            ],
            "facts": [
                {
                    "fact_id": pid,
                    "subject_person_id": pid,
                    "predicate": "full_name",
                    "value": name,
                    "evidence": [{"segment_id": pid, "quote": quote}],
                    "evidence_type": "reported",
                    "certainty": "explicit",
                }
                for pid, name, quote in [
                    ("father", "Пётр", "Мой отец Пётр."),
                    ("self", "Иван", "Меня зовут Иван."),
                ]
            ],
        }
    )
    transcript = Transcript(source_sha256="test", duration_seconds=2, segments=segments)
    sections, _ = build_sections(data, transcript)
    assert sections["full_name"] == "Иван"
    data.facts = data.facts[:1]
    data.people = data.people[:1]
    sections, _ = build_sections(data, transcript)
    assert sections["full_name"] == "Сведения не представлены"


def test_relationships_are_present_in_the_biography_not_only_in_json(tmp_path):
    transcript = Transcript.model_validate_json(
        (FIXTURES / "transcript.json").read_text(encoding="utf-8")
    )
    facts = FactSet.model_validate_json(
        (FIXTURES / "facts_expected.json").read_text(encoding="utf-8")
    )
    result = export_documents(transcript, facts, tmp_path)
    text = "\n".join(p.text for p in Document(result["biography"]).paragraphs)
    assert "Роль первого лица: отец" in text
    assert "Основания связей между людьми" in text


def test_uncle_or_someone_elses_surname_is_not_a_first_person_introduction():
    from biography.documents import build_sections
    from biography.schemas import Role

    for phrase in ("Мой дядя Иван.", "Его фамилия Иван."):
        segment = Segment(
            segment_id="s",
            start_ms=0,
            end_ms=1000,
            text_raw=phrase,
            speaker_role=Role.respondent,
        )
        data = FactSet.model_validate(
            {
                "people": [
                    {
                        "person_id": "other",
                        "name": "Иван",
                        "identity_status": "explicit",
                        "mentions": [{"segment_id": "s", "text": "Иван"}],
                    }
                ],
                "facts": [
                    {
                        "fact_id": "f",
                        "subject_person_id": "other",
                        "predicate": "full_name",
                        "value": "Иван",
                        "evidence": [{"segment_id": "s", "quote": phrase}],
                        "evidence_type": "reported",
                        "certainty": "explicit",
                    }
                ],
            }
        )
        sections, _ = build_sections(
            data,
            Transcript(source_sha256="test", duration_seconds=1, segments=[segment]),
        )
        assert sections["full_name"] == "Сведения не представлены"
