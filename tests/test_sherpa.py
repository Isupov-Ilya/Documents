import importlib.util
import json

import pytest

from biography.schemas import Settings
from biography.sherpa_backend import diarize_sherpa, load_config


def test_sherpa_missing_model_is_actionable(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {"segmentation_model": "absent.onnx", "embedding_model": "absent.onnx"}
        )
    )
    with pytest.raises(FileNotFoundError):
        load_config(config)


def test_sherpa_invalid_count_rejected_before_native_library(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {"segmentation_model": "x", "embedding_model": "y", "num_clusters": 1}
        )
    )
    with pytest.raises(ValueError, match="Число говорящих"):
        load_config(config)


@pytest.mark.skipif(
    importlib.util.find_spec("sherpa_onnx") is None, reason="Optional Sherpa dependency"
)
def test_invalid_onnx_does_not_kill_parent_process(tmp_path):
    model = tmp_path / "invalid.onnx"
    model.write_bytes(b"not an ONNX model")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"segmentation_model": str(model), "embedding_model": str(model)})
    )
    settings = Settings(
        whisper_model="unused",
        llm_model="unused",
        diarization_config=str(config),
        diarization_backend="sherpa_onnx",
    )
    with pytest.raises(RuntimeError, match="ONNX worker exited"):
        diarize_sherpa(tmp_path / "unused.wav", settings)
