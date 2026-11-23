"""Grounded DOCX exports with explicit review markers and a complete source appendix."""

import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.shared import Pt

from .io import write_json
from .provenance import semantic_warnings
from .schemas import Role
from .templates import render_profile

ROLE_NAMES = {
    Role.interviewer: "Интервьюер",
    Role.respondent: "Респондент",
    Role.third_party: "Третий участник",
    Role.unknown: "Роль не определена",
}
LABELS = {
    "full_name": "ФИО",
    "birth_year": "Год рождения",
    "date_of_birth": "Дата рождения",
    "place_of_birth": "Место рождения",
    "birth": "Рождение",
    "education": "Образование",
    "employment": "Работа",
    "employment_end": "Завершение работы",
    "relocation": "Переезд",
    "place_of_relocation": "Место переезда",
    "date_of_relocation": "Дата переезда",
}
TITLES = {
    "birth": "Рождение",
    "education": "Образование",
    "employment": "Трудовая деятельность и служба",
    "family": "Семья и упомянутые лица",
    "residence": "Места проживания",
    "other": "Другие сведения",
}
NOTICE = "Составлено по интервью. Сведения не проверены независимо. Документ требует проверки человеком."


def cleaned_text(text):
    text = re.sub(r"(?<!\w)(?:э-э|ээ+|эм+)(?!\w)[, ]*", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+([,.!?;:])", r"\1", text)


def fresh_document():
    d = Document()
    d.styles["Normal"].font.name = "Times New Roman"
    d.styles["Normal"].font.size = Pt(12)
    d.core_properties.author = "Biography"
    d.core_properties.comments = NOTICE
    return d


def unique_file(folder, name):
    path = folder / name
    n = 2
    while path.exists():
        path = folder / f"{Path(name).stem}_{n}.docx"
        n += 1
    return path


def _category(predicate):
    s = predicate.casefold()
    if "birth" in s:
        return "birth"
    if any(k in s for k in ("educat", "graduat", "degree", "school")):
        return "education"
    if any(
        k in s
        for k in ("employ", "work", "job", "career", "military", "service", "position")
    ):
        return "employment"
    if any(k in s for k in ("relocat", "residen", "address")):
        return "residence"
    return "other"


def build_sections(facts, transcript):
    segments = {s.segment_id: s for s in transcript.segments}
    people = {p.person_id: p for p in facts.people}
    declared = [
        p.person_id
        for p in facts.people
        if p.relationship_to_respondent in {"self", "respondent", "респондент"}
    ]
    primary = declared[0] if len(declared) == 1 else None
    inferred = False
    if primary is None and not declared:
        candidates = {}
        ordered = sorted(transcript.segments, key=lambda s: s.start_ms)
        indices = {s.segment_id: i for i, s in enumerate(ordered)}
        for fact in facts.facts:
            if fact.predicate != "full_name" or fact.subject_person_id not in people:
                continue
            name_pattern = re.escape(fact.value)
            own_name = (
                r"(?<!\w)(?:меня\s+зовут|моя\s+фамилия|я[\s,—–:-]+)\s*" + name_pattern
            )
            for e in fact.evidence:
                segment = segments[e.segment_id]
                if segment.speaker_role != Role.respondent:
                    continue
                text = segment.text_raw
                direct = bool(re.search(own_name, text, flags=re.I))
                index = indices[e.segment_id]
                if not direct and index:
                    previous = ordered[index - 1]
                    if (
                        previous.speaker_id in {None, segment.speaker_id}
                        and 0 <= segment.start_ms - previous.end_ms <= 350
                    ):
                        text = previous.text_raw + " " + text
                if direct or re.search(own_name, text, flags=re.I):
                    candidates[fact.subject_person_id] = not direct
        if len(candidates) == 1:
            primary, inferred = next(iter(candidates.items()))
    name = next(
        (p.name for p in facts.people if p.person_id == primary and p.name), None
    )
    if not name:
        name = next(
            (
                f.value
                for f in facts.facts
                if f.subject_person_id == primary
                and primary is not None
                and f.predicate == "full_name"
            ),
            "Сведения не представлены",
        )
    if inferred:
        name += " [Принадлежность респонденту требует проверки]"
    issues = {x["fact_id"]: x["reason"] for x in semantic_warnings(facts)}
    groups = {k: [] for k in TITLES}

    def chronology(fact):
        text = fact.date.value if fact.date else fact.value
        years = re.findall(r"(?<!\d)(?:18|19|20)\d{2}(?!\d)", text)
        return (fact.subject_person_id or "", int(years[0]) if years else 9999)

    for fact in sorted(facts.facts, key=chronology):
        person = people.get(fact.subject_person_id)
        owner = person.name if person and person.name else "Упомянутое лицо"
        if fact.subject_person_id == primary:
            owner = "Респондент"
        if fact.subject_person_id is None:
            owner = "Лицо не установлено"
        category = (
            _category(fact.predicate) if fact.subject_person_id == primary else "family"
        )
        if fact.fact_id in issues:
            line = (
                "[СОМНИТЕЛЬНО: "
                + issues[fact.fact_id]
                + "] "
                + " / ".join("«" + e.quote + "»" for e in fact.evidence)
            )
        else:
            line = (
                owner
                + ". "
                + LABELS.get(fact.predicate, "Сообщённые сведения")
                + ": "
                + fact.value
            )
            if fact.date:
                line += "; дата/период: " + fact.date.value
            if fact.certainty == "approximate" and not (
                fact.date and fact.date.approximate
            ):
                line += " (приблизительно)"
            if fact.has_conflict:
                line = "[ПРОТИВОРЕЧИЕ] " + line
            elif fact.evidence_type == "inferred":
                line = "[ВЫВОД ИИ] " + line
            elif fact.requires_review or fact.certainty == "ambiguous":
                line = (
                    "[ПРОВЕРИТЬ ПО АУДИО] "
                    if fact.certainty == "explicit"
                    else "[ТРЕБУЕТ УТОЧНЕНИЯ] "
                ) + line
        groups[category].append(line)
    relation_labels = {
        "father": "отец",
        "mother": "мать",
        "brother": "брат",
        "sister": "сестра",
        "spouse": "супруг(а)",
        "child": "ребёнок",
        "colleague": "коллега",
        "friend": "друг",
    }
    for relation in facts.relations:
        first = people[relation.subject_person_id].name or "Неустановленное лицо"
        second = people[relation.object_person_id].name or "Неустановленное лицо"
        text = f"Связь между лицами. Первое лицо: {first}. Второе лицо: {second}. Роль первого лица: {relation_labels.get(relation.relationship, relation.relationship)}."
        if relation.requires_review:
            text = "[ПРОВЕРИТЬ ПО АУДИО] " + text
        groups["family"].append(text)
    sections = {
        key: "\n".join(values) if values else "Сведения не представлены"
        for key, values in groups.items()
    }
    sections.update(
        title="Биографические сведения",
        full_name=name,
        review_notice=NOTICE,
        biography="\n\n".join(TITLES[k] + "\n" + sections[k] for k in TITLES),
    )
    return sections, issues


def _highlight(document):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    for p in document.element.body.iter(qn("w:p")):
        text = "".join(t.text or "" for t in p.iter(qn("w:t")))
        if not any(
            x in text
            for x in (
                "Сведения не представлены",
                "[ПРОТИВОРЕЧИЕ]",
                "[ВЫВОД ИИ]",
                "[ТРЕБУЕТ",
                "[СОМНИТЕЛЬНО",
                "[ПРОВЕРИТЬ",
            )
        ):
            continue
        for run in p.findall(qn("w:r")):
            pr = run.find(qn("w:rPr"))
            if pr is None:
                pr = OxmlElement("w:rPr")
                run.insert(0, pr)
            h = OxmlElement("w:highlight")
            h.set(qn("w:val"), "yellow")
            pr.append(h)


def export_documents(transcript, facts, folder: Path, profile=None):
    from .intelligence import validate_sources

    validate_sources(facts, transcript.segments)
    folder.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for clean, filename in [
        (False, "transcript_verbatim.docx"),
        (True, "transcript_clean.docx"),
    ]:
        d = fresh_document()
        d.add_heading(
            "Очищенная транскрипция" if clean else "Дословная транскрипция", 0
        )
        d.add_paragraph(NOTICE)
        if clean:
            d.add_paragraph(
                "Нормализованы пробелы, пунктуационные пробелы и явные междометия. Смысловые правки не выполнялись."
            )
        for s in transcript.segments:
            p = d.add_paragraph()
            p.add_run(
                f"{s.start_ms // 60000:02d}:{s.start_ms // 1000 % 60:02d} — {ROLE_NAMES[s.speaker_role]}: "
            ).bold = True
            p.add_run(cleaned_text(s.text_raw) if clean else s.text_raw)
            if s.requires_review:
                p.add_run(
                    " [Проверить говорящего/фрагмент]"
                ).font.highlight_color = WD_COLOR_INDEX.YELLOW
        target = unique_file(folder, filename)
        d.save(target)
        outputs["clean" if clean else "verbatim"] = str(target)
    sections, issues = build_sections(facts, transcript)
    if profile:
        d, used = render_profile(profile, sections)
        # Supplement omitted categories so a sparse template cannot discard facts.
        if "biography" not in used:
            for key, title in TITLES.items():
                if key not in used and sections[key] != "Сведения не представлены":
                    d.add_heading(title, 1)
                    d.add_paragraph(sections[key])
        d.add_paragraph(NOTICE)
    else:
        d = fresh_document()
        d.add_heading(sections["title"], 0)
        d.add_paragraph(NOTICE)
        d.add_heading(sections["full_name"], 1)
        for key, title in TITLES.items():
            d.add_heading(title, 1)
            d.add_paragraph(sections[key])
    if facts.uncertainties:
        d.add_heading("Неопределённости и замечания для проверки", 1)
        for item in facts.uncertainties:
            d.add_paragraph(item)
    d.add_heading("Основания сведений", 1)
    lookup = {s.segment_id: s for s in transcript.segments}
    covered = set()
    for number, fact in enumerate(facts.facts, 1):
        d.add_paragraph(f"Сведение {number}: " + fact.value)
        for e in fact.evidence:
            s = lookup[e.segment_id]
            covered.add(e.segment_id)
            d.add_paragraph(
                f"{s.start_ms // 60000:02d}:{s.start_ms // 1000 % 60:02d} — «{e.quote}»"
            )
    if facts.relations:
        d.add_heading("Основания связей между людьми", 2)
        for relation in facts.relations:
            for e in relation.evidence:
                covered.add(e.segment_id)
                d.add_paragraph("«" + e.quote + "»")
    # Every remaining answer stays visible; a coverage statistic is not semantic recall.
    from .coverage import uncovered_sources

    remaining = uncovered_sources(transcript.segments, facts)
    if remaining:
        d.add_heading("Материал, не отражённый в структурированных фактах", 1)
        for s in remaining:
            d.add_paragraph(ROLE_NAMES[s.speaker_role] + ": " + s.text_raw)
    _highlight(d)
    target = unique_file(folder, "biography.docx")
    d.save(target)
    outputs["biography"] = str(target)
    write_json(
        folder / "document_validation.json",
        {
            "requires_human_review": True,
            "outputs": outputs,
            "fact_count": len(facts.facts),
            "conflicts": len(facts.conflicts),
            "semantic_warnings": issues,
            "source_segments_with_fact_citations": len(covered),
            "supplemental_segments": len(remaining),
            "semantic_completeness_measured": False,
            "template_profile": str(profile) if profile else None,
        },
    )
    return outputs
