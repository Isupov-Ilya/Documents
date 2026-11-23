"""Deterministic plumbing demonstration, deliberately not an AI model benchmark."""

from pathlib import Path

from .pipeline import Run, analyze
from .schemas import FactSet, RoleResult, Transcript


def run_demo(fixtures: Path, output: Path):
    transcript = Transcript.model_validate_json(
        (fixtures / "transcript.json").read_text(encoding="utf-8")
    )
    facts = FactSet.model_validate_json(
        (fixtures / "facts_expected.json").read_text(encoding="utf-8")
    )
    roles = RoleResult.model_validate_json(
        (fixtures / "roles.json").read_text(encoding="utf-8")
    )

    def scripted(system, payload, schema):
        if schema is FactSet and payload.get("known_facts"):
            return FactSet(
                people=[], facts=[]
            )  # Oracle fixture already contains its full expected facts.
        return (roles if schema is RoleResult else facts).model_copy(deep=True)

    with Run(output, "synthetic_fixture_no_models") as run:
        run.report["warning"] = (
            "Демонстрация на заранее размеченном тексте. ASR, диаризация и LLM НЕ выполняются."
        )
        analyze(transcript, scripted, run, chunk_chars=20000)
