"""Derive a Sherpa-compatible WeSpeaker ONNX, preserving verified source bytes.

Metadata follows k2-fsa/sherpa-onnx/scripts/wespeaker/add_meta_data.py.
No weights or expected source checksums are changed.
"""

import argparse
import json
from pathlib import Path

import onnx

from biography.io import sha256, write_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", type=Path)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if a.source.resolve() == a.output.resolve():
        raise ValueError("Source must remain unchanged")
    manifest = json.loads((a.source.parent / "download-manifest.json").read_text())
    record = next(x for x in manifest if x["file"] == a.source.name)
    if sha256(a.source) != record["sha256"]:
        raise ValueError("Verified source checksum mismatch")
    model = onnx.load(a.source)
    dim = model.graph.output[0].type.tensor_type.shape.dim[1].dim_value
    if len(model.graph.input) != 1 or len(model.graph.output) != 1 or dim <= 0:
        raise ValueError("Unsupported WeSpeaker model shape")
    metadata = {x.key: x.value for x in model.metadata_props}
    metadata.update(
        framework="wespeaker",
        language="English",
        sample_rate="16000",
        output_dim=str(dim),
        normalize_samples="0",
        url="https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM",
        comment="Metadata-only derivative for Sherpa ONNX; source preserved",
    )
    onnx.helper.set_model_props(model, metadata)
    onnx.checker.check_model(model)
    onnx.save(model, a.output)
    write_json(
        a.output.with_suffix(".derivation.json"),
        {
            "source": record,
            "derived_sha256": sha256(a.output),
            "changes": "ONNX metadata only",
            "procedure": "https://github.com/k2-fsa/sherpa-onnx/blob/master/scripts/wespeaker/add_meta_data.py",
        },
    )


if __name__ == "__main__":
    main()
