"""Validate actual frozen output, not a scripted model substitute."""

import argparse
import json
import zipfile
from pathlib import Path

from docx import Document

from biography.intelligence import validate_sources
from biography.schemas import FactSet, Transcript


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--project", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    a = p.parse_args()

    def read(name):
        return json.loads((a.project / name).read_text(encoding="utf-8"))

    report = read("quality_report.json")
    assert report["status"] == "completed", report
    assert report["mode"] in {"audio_models", "saved_transcript_models"}
    transcript = Transcript.model_validate(read("transcript.json"))
    facts = FactSet.model_validate(read("facts.json"))
    validate_sources(facts, transcript.segments)
    assert transcript.source_kind == "audio"
    assert len(facts.facts) >= 5, "No meaningful facts extracted"
    assert len(facts.people) >= 3, (
        "Respondent, father and colleague must remain distinct"
    )
    assert facts.conflicts, "Both contradictory father birth dates must be retained"
    assert all(len(c.fact_ids) >= 2 for c in facts.conflicts)
    outputs = read("document_validation.json")["outputs"]
    documents = {k: Document(v) for k, v in outputs.items()}
    assert set(documents) == {"verbatim", "clean", "biography"}
    text = "\n".join(p.text for p in documents["biography"].paragraphs)
    for table in documents["biography"].tables:
        text += "\n" + "\n".join(cell.text for row in table.rows for cell in row.cells)
    for year in ["1980", "1950", "1951", "2002", "2005", "2010"]:
        assert year in text, f"Lost relevant date {year}"
    assert "требует проверки человеком" in text
    assert "ПРОТИВОРЕЧИЕ" in text
    for path in outputs.values():
        with zipfile.ZipFile(path) as z:
            assert z.testzip() is None
            xml = "\n".join(
                z.read(n).decode("utf-8", errors="replace")
                for n in z.namelist()
                if n.endswith(".xml")
            )
            assert "SECRET_TEMPLATE_PERSON" not in xml
    assert documents["biography"].tables, "Template table was lost"
    result = {
        "status": "passed",
        "actual_models": True,
        "fact_count": len(facts.facts),
        "person_count": len(facts.people),
        "conflict_count": len(facts.conflicts),
        "roles_source": report["roles_source"],
        "source_kind": transcript.source_kind,
        "document_count": len(outputs),
        "elapsed_seconds": report["elapsed_seconds"],
        "peak_process_tree_rss_bytes_sampled": report[
            "peak_process_tree_rss_bytes_sampled"
        ],
        "semantic_accuracy_statistically_validated": False,
        "limitations": [
            "One synthetic sample. No independent real interview corpus.",
            "Human verification required, including names and uncertain facts.",
            "This test does not establish 60-minute factual completeness or GPU support.",
        ],
    }
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
