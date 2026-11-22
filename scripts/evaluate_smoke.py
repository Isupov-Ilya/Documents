"""Measure a synthetic smoke run; never label turn-level diagnostics as DER."""

import argparse
import itertools
import json
import re
from pathlib import Path

from biography.io import write_json


def tokens(text):
    return re.findall(r"[\w]+", text.casefold().replace("ё", "е"), flags=re.UNICODE)


def distance(reference, hypothesis):
    previous = list(range(len(hypothesis) + 1))
    for i, a in enumerate(reference, 1):
        row = [i]
        for j, b in enumerate(hypothesis, 1):
            row.append(min(previous[j] + 1, row[-1] + 1, previous[j - 1] + (a != b)))
        previous = row
    return previous[-1]


def evaluate(reference, asr, turns):
    ref = tokens(" ".join(t["text"] for t in reference["turns"]))
    hyp = tokens(" ".join(s["text_raw"] for s in asr))
    truth = sorted({t["speaker"] for t in reference["turns"]})
    predicted = sorted({t["speaker_id"] for t in turns})

    def overlap(a, b):
        return max(
            0, min(a["end"], b["end_ms"] / 1000) - max(a["start"], b["start_ms"] / 1000)
        )

    mappings = [
        dict(zip(predicted, p)) for p in itertools.permutations(truth, len(predicted))
    ]
    if not mappings:
        raise ValueError("Cannot align unexpected speaker count")

    def score(mapping):
        return sum(
            overlap(a, b)
            for a in reference["turns"]
            for b in turns
            if mapping[b["speaker_id"]] == a["speaker"]
        )

    mapping = max(mappings, key=score)
    centers = []
    for a in reference["turns"]:
        middle = (a["start"] + a["end"]) * 500
        speakers = {
            mapping[t["speaker_id"]]
            for t in turns
            if t["start_ms"] <= middle < t["end_ms"]
        }
        centers.append(
            {
                "expected": a["speaker"],
                "predicted": sorted(speakers),
                "correct": speakers == {a["speaker"]},
            }
        )
    return {
        "synthetic": True,
        "normalization": "lowercase, ё→е, punctuation removed; numbers NOT expanded",
        "reference_words": len(ref),
        "hypothesis_words": len(hyp),
        "word_edit_distance": distance(ref, hyp),
        "wer": distance(ref, hyp) / len(ref),
        "speaker_mapping_by_maximum_overlap": mapping,
        "utterance_center_speaker_accuracy": sum(x["correct"] for x in centers)
        / len(centers),
        "utterance_centers": centers,
        "der_measured": False,
        "limitations": [
            "Single synthetic sample; not an independent acceptance corpus.",
            "TTS utterance bounds include silence and are not speech-activity annotations.",
            "Fixed two clusters used from known scenario; automatic speaker count not validated.",
            "No manual semantic fact scoring is included in this metric.",
        ],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reference", type=Path, required=True)
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()

    def read(p):
        return json.loads(p.read_text(encoding="utf-8"))

    report = evaluate(
        read(a.reference),
        read(a.run / "asr_raw.json"),
        read(a.run / "diarization.json"),
    )
    write_json(a.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
