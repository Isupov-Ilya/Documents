"""Create an explicitly synthetic two-voice Russian smoke fixture, entirely locally."""

import argparse
import gc
import io
import json
import wave
from pathlib import Path

import onnxruntime as ort
from piper import PiperVoice
from piper.config import PiperConfig

from biography.io import sha256, write_json
from biography.offline import offline_mode

DIALOGUE = [
    (
        "denis",
        "Начинаю интервью. Я интервьюер. Назовите ваше имя и расскажите, где вы родились.",
    ),
    ("dmitri", "Меня зовут Иван Сергеевич Волков. Я родился в Самаре в 1980 году."),
    ("denis", "Когда вы окончили университет и где работали?"),
    (
        "dmitri",
        "В 2002 году я окончил технический университет. С 2005 года работал инженером на заводе Вектор.",
    ),
    ("denis", "Что вы можете рассказать о семье?"),
    (
        "dmitri",
        "Мой отец Пётр Волков родился примерно в 1950 году. Потом он переехал в Казань.",
    ),
    ("dmitri", "Возможно, отец родился в 1951 году. Точный год я не помню."),
    ("denis", "А кто ещё переехал?"),
    ("dmitri", "Мой коллега Иван переехал в Москву. Он уволился в 2010 году."),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    pieces = {}
    rate = None
    with offline_mode():
        for name in ("denis", "dmitri"):
            path = args.models / f"ru/ru_RU/{name}/medium/ru_RU-{name}-medium.onnx"
            config = json.loads(Path(str(path) + ".json").read_text())
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            voice = PiperVoice(
                config=PiperConfig.from_dict(config),
                session=ort.InferenceSession(
                    str(path), sess_options=options, providers=["CPUExecutionProvider"]
                ),
            )
            for i, (speaker, text) in enumerate(DIALOGUE):
                if speaker != name:
                    continue
                buffer = io.BytesIO()
                with wave.open(buffer, "wb") as output:
                    voice.synthesize_wav(text, output)
                with wave.open(io.BytesIO(buffer.getvalue()), "rb") as audio:
                    assert audio.getnchannels() == 1 and audio.getsampwidth() == 2
                    if rate is not None:
                        assert rate == audio.getframerate()
                    rate = audio.getframerate()
                    pieces[i] = audio.readframes(audio.getnframes())
            del voice
            gc.collect()
    frames = rate
    reference = []
    path = args.output / "interview.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\0\0" * rate)
        for i, (speaker, text) in enumerate(DIALOGUE):
            data = pieces[i]
            start = frames / rate
            audio.writeframes(data)
            frames += len(data) // 2
            reference.append(
                {"speaker": speaker, "text": text, "start": start, "end": frames / rate}
            )
            audio.writeframes(b"\0\0" * rate)
            frames += rate
    write_json(
        args.output / "reference.json",
        {
            "synthetic": True,
            "tts": "piper-tts",
            "voices": ["denis", "dmitri"],
            "voice_dataset_licenses": "CC0 per publisher MODEL_CARD",
            "sha256": sha256(path),
            "duration_seconds": frames / rate,
            "turns": reference,
            "note": "Turn intervals are TTS containers, not manually annotated speech activity.",
        },
    )
    print(path, frames / rate, flush=True)


if __name__ == "__main__":
    main()
