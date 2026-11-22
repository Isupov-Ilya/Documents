import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_word_error_counts_insertions_deletions_and_substitutions():
    evaluate = load_script("evaluate_smoke")
    assert evaluate.distance(["a", "b"], ["a", "c", "d"]) == 2
    assert evaluate.distance(["a"], []) == 1
    assert evaluate.tokens("Ёж, родился в 1980 году.") == [
        "еж",
        "родился",
        "в",
        "1980",
        "году",
    ]


def test_model_download_rejects_wrong_bytes_and_preserves_existing_file(
    tmp_path, monkeypatch
):
    prepare = load_script("prepare_models")
    trusted = b"correct weights"
    info = {
        "sha": "abc",
        "siblings": [
            {
                "rfilename": "model.bin",
                "size": len(trusted),
                "lfs": {"sha256": hashlib.sha256(trusted).hexdigest()},
                "blobId": "not-used",
            }
        ],
    }

    def respond(url, **kwargs):
        return io.BytesIO(
            json.dumps(info).encode() if "/api/models/" in url else b"corrupt weights"
        )

    monkeypatch.setattr(prepare.urllib.request, "urlopen", respond)
    (tmp_path / "model.bin").write_bytes(b"existing bytes")
    with pytest.raises(ValueError, match="Checksum or size mismatch"):
        prepare.fetch("example/model", "abc", ["model.bin"], tmp_path)
    assert (tmp_path / "model.bin").read_bytes() == b"existing bytes"
    assert not list(tmp_path.glob("*.partial"))


def test_verified_model_download_is_reused_without_fetching_weight(
    tmp_path, monkeypatch
):
    prepare = load_script("prepare_models")
    content = b"good weights"
    (tmp_path / "model.bin").write_bytes(content)
    info = {
        "sha": "abc",
        "siblings": [
            {
                "rfilename": "model.bin",
                "size": len(content),
                "lfs": {"sha256": hashlib.sha256(content).hexdigest()},
                "blobId": "not-used",
            }
        ],
    }

    def respond(url, **kwargs):
        assert "/api/models/" in url
        return io.BytesIO(json.dumps(info).encode())

    monkeypatch.setattr(prepare.urllib.request, "urlopen", respond)
    records = prepare.fetch("example/model", "abc", ["model.bin"], tmp_path)
    assert records[0]["sha256"] == hashlib.sha256(content).hexdigest()
