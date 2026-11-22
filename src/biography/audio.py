import json
import subprocess
import wave
from pathlib import Path

from .io import sha256


class AudioError(ValueError):
    pass


def prepare(source: Path, target: Path, ffmpeg="ffmpeg", ffprobe="ffprobe") -> dict:
    source = source.resolve(strict=True)
    if source.suffix.lower() not in {".m4a", ".mp3", ".wav"}:
        raise AudioError("Поддерживаются только M4A, MP3 и WAV")
    if not source.is_file() or source.stat().st_size == 0:
        raise AudioError("Аудиофайл пуст или недоступен")
    if source == target.resolve():
        raise AudioError("Исходный файл нельзя перезаписывать")
    digest = sha256(source)
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-show_streams",
            "-of",
            "json",
            str(source),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        raise AudioError("FFprobe: " + result.stderr[-2000:])
    if not any(
        s.get("codec_type") == "audio"
        for s in json.loads(result.stdout).get("streams", [])
    ):
        raise AudioError("В файле нет аудиодорожки")
    temporary = target.with_suffix(".partial.wav")
    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-y",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(temporary),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode:
            raise AudioError("FFmpeg: " + result.stderr[-2000:])
        with wave.open(str(temporary), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
            if duration <= 0:
                raise AudioError("Пустая аудиодорожка")
        if sha256(source) != digest:
            raise AudioError("Исходный файл изменился во время обработки")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(source), "sha256": digest, "duration_seconds": duration}
