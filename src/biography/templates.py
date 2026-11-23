"""Versioned, explicit template mapping. Source personal text is never copied by default."""

import hashlib
import json
import uuid
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from .io import sha256, write_json

FIELDS = {
    "missing": "Сведения не представлены",
    "title": "Заголовок",
    "full_name": "ФИО",
    "birth": "Рождение",
    "education": "Образование",
    "employment": "Работа и служба",
    "family": "Семья и другие лица",
    "residence": "Места проживания",
    "other": "Другие сведения",
    "biography": "Вся биография",
    "review_notice": "Отметка о проверке",
}
# Restrict the cloned layout to structural and formatting elements, not hidden content.
STRUCTURAL = {"tbl", "tblGrid", "gridCol", "tr", "tc", "p", "r", "t"}
FORMAT = {
    "pPr",
    "rPr",
    "tblPr",
    "trPr",
    "tcPr",
    "tblBorders",
    "tcBorders",
    "top",
    "left",
    "bottom",
    "right",
    "insideH",
    "insideV",
    "b",
    "bCs",
    "i",
    "iCs",
    "sz",
    "szCs",
    "rFonts",
    "color",
    "highlight",
    "u",
    "strike",
    "caps",
    "smallCaps",
    "vertAlign",
    "lang",
    "rtl",
    "jc",
    "spacing",
    "ind",
    "tabs",
    "tab",
    "keepNext",
    "keepLines",
    "pageBreakBefore",
    "widowControl",
    "contextualSpacing",
    "pBdr",
    "shd",
    "textDirection",
    "textAlignment",
    "outlineLvl",
    "tblW",
    "tblInd",
    "tblLayout",
    "tblCellMar",
    "tcMar",
    "tcW",
    "gridSpan",
    "vMerge",
    "vAlign",
    "trHeight",
    "cantSplit",
    "tblHeader",
    "noWrap",
    "tblLook",
    "tblOverlap",
    "bidiVisual",
    "tblCaption",
}
# tblCaption may contain personal text; never retain its value.
FORMAT.discard("tblCaption")


def source_document(path):
    import zipfile

    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > 200 * 1024 * 1024:
            raise ValueError("Слишком большой DOCX-образец")
    return Document(path)


def suggest(text):
    lower = text.casefold()
    for roots, field in [
        (("образован", "учеб", "университет"), "education"),
        (("работ", "труд", "должност"), "employment"),
        (("семь", "родствен", "дети"), "family"),
        (("рожд", "родил"), "birth"),
        (("прожив", "местожит"), "residence"),
        (("фамил", "фио", "ф.и.о"), "full_name"),
    ]:
        if any(s in lower for s in roots):
            return field
    return "missing"


def analyze_samples(paths):
    documents = [source_document(p) for p in paths]
    if not documents:
        raise ValueError("Выберите DOCX-образец")
    all_texts = []
    for document in documents:
        all_texts.append(
            {
                "".join(t.text or "" for t in p.iter(qn("w:t"))).strip()
                for p in document.element.body.iter(qn("w:p"))
            }
        )
    blocks = []
    for p in documents[0].element.body.iter(qn("w:p")):
        text = "".join(t.text or "" for t in p.iter(qn("w:t"))).strip()
        if text:
            blocks.append(
                {
                    "slot": f"slot_{len(blocks) + 1:04d}",
                    "sample_text": text,
                    "field": suggest(text),
                    "fixed": False,
                    "fixed_confirmed": False,
                    "common_candidate": all(text in group for group in all_texts[1:])
                    and len(paths) > 1,
                }
            )
    if not blocks:
        raise ValueError("В образце нет текстовых абзацев")
    return blocks


def _scrub(element):
    tag = element.tag.split("}")[-1]
    if tag not in STRUCTURAL | FORMAT:
        return None
    result = OxmlElement("w:" + tag)
    for key, value in element.attrib.items():
        # Only standard Word formatting attributes, no revision authors or relationships.
        if key.startswith("{" + qn("w:val").split("}")[0][1:] + "}"):
            result.set(key, value)
    if tag == "t":
        return result
    for child in element:
        clean = _scrub(child)
        if clean is not None:
            result.append(clean)
    return result


def create_profile(paths, choices, destination: Path, name):
    destination.mkdir(parents=True, exist_ok=False)
    source = source_document(paths[0])
    blocks = analyze_samples(paths)
    if len(choices) != len(blocks):
        raise ValueError("Изменилась структура образца")
    document = Document()
    for child in list(document.element.body):
        if child.tag != qn("w:sectPr"):
            document.element.body.remove(child)
    saved = []
    index = 0
    for element in source.element.body:
        if element.tag not in {qn("w:p"), qn("w:tbl")}:
            continue
        original_paragraphs = (
            [element] if element.tag == qn("w:p") else list(element.iter(qn("w:p")))
        )
        clean = _scrub(element)
        clean_paragraphs = (
            [clean] if clean.tag == qn("w:p") else list(clean.iter(qn("w:p")))
        )
        for original, paragraph in zip(original_paragraphs, clean_paragraphs):
            text = "".join(t.text or "" for t in original.iter(qn("w:t"))).strip()
            if not text:
                continue
            block = blocks[index]
            choice = choices[index]
            index += 1
            fixed = bool(choice.get("fixed"))
            if fixed and choice.get("fixed_confirmed") is not True:
                raise ValueError(
                    "Постоянный блок должен быть явно подтверждён как не содержащий чужие персональные сведения"
                )
            field = choice.get("field", "missing")
            if field not in FIELDS:
                raise ValueError("Неизвестное поле шаблона")
            for run in list(paragraph):
                if run.tag != qn("w:pPr"):
                    paragraph.remove(run)
            run = OxmlElement("w:r")
            # Preserve direct run formatting without retaining source text or revisions.
            original_run = original.find(qn("w:r"))
            if original_run is not None:
                props = original_run.find(qn("w:rPr"))
                if props is not None:
                    safe_props = _scrub(props)
                    if safe_props is not None:
                        run.append(safe_props)
            t = OxmlElement("w:t")
            t.text = "{{" + block["slot"] + "}}"
            run.append(t)
            paragraph.append(run)
            saved.append(
                {
                    "slot": block["slot"],
                    "field": field,
                    "fixed_text": text if fixed else None,
                    "fixed_confirmed": fixed,
                    "sample_hash": hashlib.sha256(text.encode()).hexdigest(),
                }
            )
        document.element.body.insert(len(document.element.body) - 1, clean)
    for a, b in zip(document.sections, source.sections):
        for prop in (
            "page_width",
            "page_height",
            "top_margin",
            "bottom_margin",
            "left_margin",
            "right_margin",
        ):
            setattr(a, prop, getattr(b, prop))
    document.core_properties.author = "Biography"
    document.core_properties.title = name
    document.core_properties.comments = (
        "Sanitized template; no source personal paragraphs retained."
    )
    document.save(destination / "layout.docx")
    write_json(
        destination / "profile.json",
        {
            "schema_version": "1.0",
            "profile_id": uuid.uuid4().hex,
            "name": name,
            "version": 1,
            "previous_profile_id": None,
            "slots": saved,
            "source_hashes": [sha256(Path(p)) for p in paths],
        },
    )
    return destination / "profile.json"


def render_profile(path, sections):
    path = Path(path)
    profile = json.loads(path.read_text(encoding="utf-8"))
    if profile.get("schema_version") != "1.0":
        raise ValueError("Неподдерживаемая версия профиля")
    document = Document(path.parent / "layout.docx")
    slots = {s["slot"]: s for s in profile["slots"]}
    for p in document.element.body.iter(qn("w:p")):
        text = "".join(t.text or "" for t in p.iter(qn("w:t")))
        if not text.startswith("{{") or not text.endswith("}}"):
            continue
        slot = slots[text[2:-2]]
        if slot["fixed_text"] is not None:
            if slot.get("fixed_confirmed") is not True:
                raise ValueError("Неподтверждённый постоянный блок")
            value = slot["fixed_text"]
        else:
            value = sections.get(slot["field"], "Сведения не представлены")
        for t in p.iter(qn("w:t")):
            t.text = ""
        texts = list(p.iter(qn("w:t")))
        if texts:
            texts[0].text = value
    return document, {s["field"] for s in profile["slots"] if s["fixed_text"] is None}
