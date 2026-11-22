import importlib.metadata
import shutil
from pathlib import Path


def check_environment(settings):
    errors, versions = [], {}
    for name in (
        "pydantic",
        "psutil",
        "faster-whisper",
        "sherpa-onnx"
        if settings.diarization_backend == "sherpa_onnx"
        else "pyannote.audio",
        "llama-cpp-python",
    ):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            errors.append(f"Не установлен пакет: {name}")
    for name in ("ffmpeg", "ffprobe"):
        if not shutil.which(getattr(settings, name)):
            errors.append(f"Не найден {name}")
    whisper = Path(settings.whisper_model)
    for name in ("model.bin", "config.json", "tokenizer.json"):
        if not (whisper / name).is_file():
            errors.append(f"Не найден файл Whisper: {whisper / name}")
    for name in ("diarization_config", "llm_model"):
        path = Path(getattr(settings, name))
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"Не найдена локальная модель/конфигурация: {path}")
    if (
        settings.diarization_backend == "sherpa_onnx"
        and Path(settings.diarization_config).is_file()
    ):
        from .sherpa_backend import load_config

        try:
            load_config(Path(settings.diarization_config))
        except (OSError, ValueError) as error:
            errors.append(f"Некорректная ONNX-конфигурация: {error}")
    return {
        "status": "prerequisites_missing"
        if errors
        else "paths_present_models_unvalidated",
        "versions": versions,
        "errors": errors,
        "note": "Проверка наличия; совместимость, качество и GPU проверяются реальным запуском.",
    }
