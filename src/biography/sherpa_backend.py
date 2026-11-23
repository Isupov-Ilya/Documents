"""Alternative local speaker diarization without Torch or Hub authentication."""

import json
import subprocess
import sys
import wave
from pathlib import Path

from pydantic import Field

from .schemas import Record, Turn


class SherpaConfig(Record):
    segmentation_model: str
    embedding_model: str
    num_clusters: int = Field(default=-1, ge=-1, le=3)
    cluster_threshold: float = Field(default=0.5, gt=0, lt=2)


def load_config(path: Path):
    config = SherpaConfig.model_validate_json(path.read_text(encoding="utf-8"))
    if config.num_clusters in (0, 1):
        raise ValueError("Число говорящих должно быть 2, 3 или -1 (автоматически)")
    for field in ("segmentation_model", "embedding_model"):
        value = (path.parent / Path(getattr(config, field))).resolve(strict=True)
        if not value.is_file():
            raise ValueError(f"Не найден ONNX-файл: {value}")
        setattr(config, field, str(value))
    return config


def _diarize_worker(wav: Path, settings):
    import numpy as np
    import sherpa_onnx as so

    config = load_config(Path(settings.diarization_config))
    if settings.device != "cpu":
        raise ValueError("Альтернативный адаптер пока проверяется только на CPU")
    if (
        config.num_clusters != -1
        and not settings.min_speakers <= config.num_clusters <= settings.max_speakers
    ):
        raise ValueError("Число кластеров вне диапазона настроек")
    sc = so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=config.segmentation_model
            ),
            num_threads=settings.threads,
            provider="cpu",
        ),
        embedding=so.SpeakerEmbeddingExtractorConfig(
            model=config.embedding_model, num_threads=settings.threads, provider="cpu"
        ),
        clustering=so.FastClusteringConfig(
            num_clusters=config.num_clusters, threshold=config.cluster_threshold
        ),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not sc.validate():
        raise ValueError("Некорректная конфигурация ONNX-диаризации")
    engine = so.OfflineSpeakerDiarization(sc)
    with wave.open(str(wav), "rb") as audio:
        if (
            audio.getnchannels() != 1
            or audio.getsampwidth() != 2
            or audio.getframerate() != engine.sample_rate
        ):
            raise ValueError("Ожидался подготовленный PCM16 mono 16 kHz")
        samples = (
            np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(
                np.float32
            )
            / 32768.0
        )
    items = engine.process(samples).sort_by_start_time()
    turns = [
        Turn(
            start_ms=max(0, round(r.start * 1000)),
            end_ms=round(r.end * 1000),
            speaker_id=f"SPEAKER_{r.speaker:02d}",
        )
        for r in items
        if r.end > r.start
    ]
    count = len({t.speaker_id for t in turns})
    if not settings.min_speakers <= count <= settings.max_speakers:
        raise ValueError(
            f"Обнаружено голосов: {count}; ожидалось {settings.min_speakers}–{settings.max_speakers}. "
            "Уточните num_clusters в конфигурации диаризации; автоматическая оценка не подтверждена."
        )
    return turns


def diarize_sherpa(wav: Path, settings):
    from .runtime import process_command

    # Isolate native libraries: a malformed model must not kill the orchestrator.
    load_config(Path(settings.diarization_config))
    response = subprocess.run(
        process_command("diarization", str(wav.resolve())),
        input=settings.model_dump_json(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if response.returncode:
        raise RuntimeError(
            f"ONNX worker exited {response.returncode}: {response.stderr[-3000:]}"
        )
    return [Turn.model_validate(x) for x in json.loads(response.stdout)]


if __name__ == "__main__":
    from .runtime import configure_stdio

    configure_stdio()
    from .offline import offline_mode
    from .schemas import Settings

    with offline_mode():
        settings = Settings.model_validate_json(sys.stdin.read())
        turns = _diarize_worker(Path(sys.argv[1]), settings)
        print(json.dumps([x.model_dump() for x in turns]))
