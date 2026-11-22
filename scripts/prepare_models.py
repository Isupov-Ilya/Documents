"""Explicit network preparation, never imported by the offline application.

Download pinned public model files, verify publisher LFS SHA-256 or Git blob
SHA-1, retain source revisions, licenses and SHA-256 for transfer auditing.
"""

import argparse
import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path

REPOS = {
    "Qwen/Qwen2.5-7B-Instruct-GGUF": (
        "bb5d59e06d9551d752d08b292a50eb208b07ab1f",
        "llm-7b",
        [
            "qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",
            "qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf",
            "LICENSE",
            "README.md",
        ],
    ),
    "Systran/faster-whisper-small": (
        "536b0662742c02347bc0e980a01041f333bce120",
        "whisper-small",
        ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt", "README.md"],
    ),
    "rhasspy/piper-voices": (
        "6b9cfaacb4fb2e02a9621227e3c1a48bec9710c3",
        "piper",
        [
            f"ru/ru_RU/{name}/medium/{file}"
            for name in ("denis", "dmitri")
            for file in (
                "MODEL_CARD",
                f"ru_RU-{name}-medium.onnx",
                f"ru_RU-{name}-medium.onnx.json",
            )
        ],
    ),
    "Systran/faster-whisper-tiny": (
        "d90ca5fe260221311c53c58e660288d3deb8d356",
        "whisper-tiny",
        ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt", "README.md"],
    ),
    "csukuangfj/sherpa-onnx-pyannote-segmentation-3-0": (
        "9403a6902bb58e3d5ae8c7e77c3422de279db2e0",
        "segmentation",
        ["model.onnx", "LICENSE", "README.md"],
    ),
    "Wespeaker/wespeaker-voxceleb-resnet34-LM": (
        "f0c48c298fd835726c27956a5d617bad7115627e",
        "embedding",
        ["voxceleb_resnet34_LM.onnx", "README.md"],
    ),
    "Qwen/Qwen2.5-3B-Instruct-GGUF": (
        "7dabda4d13d513e3e842b20f0d435c732f172cbe",
        "llm",
        ["qwen2.5-3b-instruct-q4_k_m.gguf", "LICENSE", "README.md"],
    ),
}


def fetch(repo, revision, files, target):
    url = f"https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true"
    with urllib.request.urlopen(url, timeout=60) as response:
        metadata = json.load(response)
    if metadata["sha"] != revision:
        raise ValueError("Unexpected repository revision")
    entries = {x["rfilename"]: x for x in metadata["siblings"]}
    target.mkdir(parents=True, exist_ok=True)
    records = []
    for filename in files:
        info = entries[filename]
        path = target / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        expected = info.get("lfs", {}).get("sha256")

        def valid(p):
            if not p.is_file() or p.stat().st_size != info["size"]:
                return False
            if expected:
                with p.open("rb") as stream:
                    return hashlib.file_digest(stream, "sha256").hexdigest() == expected
            content = p.read_bytes()
            return (
                hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
                == info["blobId"]
            )

        if not valid(path):
            temporary = path.with_suffix(path.suffix + ".partial")
            try:
                print("Downloading", repo, filename, info["size"], flush=True)
                url = (
                    f"https://huggingface.co/{repo}/resolve/{revision}/"
                    + urllib.parse.quote(filename, safe="/")
                    + "?download=true"
                )
                with (
                    urllib.request.urlopen(url, timeout=120) as response,
                    temporary.open("wb") as output,
                ):
                    while block := response.read(4 * 1024 * 1024):
                        output.write(block)
                if not valid(temporary):
                    raise ValueError(
                        f"Checksum or size mismatch: {filename}; "
                        f"received {temporary.stat().st_size} bytes, expected {info['size']}"
                    )
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        records.append(
            {
                "repo": repo,
                "revision": revision,
                "file": filename,
                "size": path.stat().st_size,
                "sha256": digest,
                "publisher_lfs_sha256": expected,
                "publisher_git_blob": info["blobId"],
            }
        )
    (target / "download-manifest.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--only",
        choices=["all", "asr", "asr-small", "diarization", "llm", "llm-7b", "tts"],
        default="all",
    )
    args = parser.parse_args()
    for repo, (revision, folder, files) in REPOS.items():
        kind = (
            "tts"
            if folder == "piper"
            else "asr-small"
            if folder == "whisper-small"
            else "asr"
            if folder.startswith("whisper")
            else "llm-7b"
            if folder == "llm-7b"
            else "llm"
            if folder == "llm"
            else "diarization"
        )
        if args.only in ("all", kind):
            fetch(repo, revision, files, args.output / folder)


if __name__ == "__main__":
    main()
