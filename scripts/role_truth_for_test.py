"""Synthetic test role correction, explicitly NOT an automatic role classifier."""

import argparse
import json
from pathlib import Path

from evaluate_smoke import evaluate

p = argparse.ArgumentParser()
p.add_argument("--project", type=Path, required=True)
p.add_argument("--reference", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


report = evaluate(
    read(a.reference),
    read(a.project / "asr_raw.json"),
    read(a.project / "diarization.json"),
)
roles = {
    speaker: ("interviewer" if voice == "denis" else "respondent")
    for speaker, voice in report["speaker_mapping_by_maximum_overlap"].items()
}
a.output.write_text(json.dumps(roles) + "\n", encoding="utf-8")
