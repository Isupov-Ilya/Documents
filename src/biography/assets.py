"""Explicit preparation step. Audio processing never invokes this downloader."""

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

from .io import sha256, write_json
from .runtime import local_tool


def manifest():
    return json.loads(
        (Path(__file__).parent / "resources" / "model_manifest.json").read_text(
            encoding="utf-8"
        )
    )


def verified(path, record):
    return (
        path.is_file()
        and path.stat().st_size == record["size"]
        and sha256(path) == record["sha256"]
    )


def download_one(record, root, progress=lambda *a: None, cancelled=lambda: False):
    path = root / record["relative_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    if verified(path, record):
        progress(record["relative_path"], record["size"], record["size"])
        return path
    temporary = path.with_suffix(path.suffix + ".partial")
    start = temporary.stat().st_size if temporary.exists() else 0
    if start >= record["size"]:
        temporary.unlink()
        start = 0
    url = (
        f"https://huggingface.co/{record['repo']}/resolve/{record['revision']}/"
        + urllib.parse.quote(record["file"], safe="/")
        + "?download=true"
    )
    headers = {"Range": f"bytes={start}-"} if start else {}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=120) as response:
        if start and response.status == 206:
            if not response.headers.get("Content-Range", "").startswith(
                f"bytes {start}-"
            ):
                raise ValueError("Сервер вернул неверный диапазон файла")
        elif response.status == 200:
            start = 0
        else:
            raise ValueError(f"Неподдерживаемый ответ загрузки: {response.status}")
        with temporary.open("ab" if start else "wb") as stream:
            done = start
            while block := response.read(4 * 1024 * 1024):
                if cancelled():
                    raise InterruptedError(
                        "Подготовка остановлена; загрузку можно продолжить"
                    )
                stream.write(block)
                done += len(block)
                progress(record["relative_path"], done, record["size"])
                if done > record["size"]:
                    raise ValueError("Размер загрузки превышает ожидаемый")
    if not verified(temporary, record):
        temporary.unlink(missing_ok=True)
        raise ValueError(
            "Контрольная сумма модели не совпала: " + record["relative_path"]
        )
    temporary.replace(path)
    return path


def prepare_embedding(root):
    import onnx

    source = root / "embedding" / "voxceleb_resnet34_LM.onnx"
    record = next(
        r for r in manifest() if r["relative_path"] == "embedding/" + source.name
    )
    if not verified(source, record):
        raise ValueError("Исходная embedding-модель не прошла проверку")
    target = source.with_name("voxceleb_resnet34_LM.sherpa.onnx")
    stamp = target.with_suffix(".derivation.json")
    if target.is_file() and stamp.is_file():
        old = json.loads(stamp.read_text(encoding="utf-8"))
        if old.get("source_sha256") == record["sha256"] and old.get(
            "derived_sha256"
        ) == sha256(target):
            return target
    model = onnx.load(source)
    dim = model.graph.output[0].type.tensor_type.shape.dim[1].dim_value
    if dim <= 0:
        raise ValueError("Неизвестная размерность speaker embedding")
    metadata = {p.key: p.value for p in model.metadata_props}
    metadata.update(
        framework="wespeaker",
        language="English",
        sample_rate="16000",
        output_dim=str(dim),
        normalize_samples="0",
        url="https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM",
        comment="Metadata-only derivative; original weights preserved",
    )
    onnx.helper.set_model_props(model, metadata)
    onnx.checker.check_model(model)
    temporary = target.with_suffix(".partial.onnx")
    onnx.save(model, temporary)
    temporary.replace(target)
    write_json(
        stamp,
        {
            "source_sha256": record["sha256"],
            "derived_sha256": sha256(target),
            "changes": "metadata only",
        },
    )
    return target


def configure(data_root):
    from .schemas import Settings

    models = data_root / "models"
    prepare_embedding(models)
    folder = models / "diarization-onnx"
    folder.mkdir(exist_ok=True)
    write_json(
        folder / "config.json",
        {
            "segmentation_model": "../segmentation/model.onnx",
            "embedding_model": "../embedding/voxceleb_resnet34_LM.sherpa.onnx",
            "num_clusters": -1,
            "cluster_threshold": 0.5,
        },
    )
    settings = Settings(
        whisper_model="models/whisper-small",
        diarization_config="models/diarization-onnx/config.json",
        diarization_backend="sherpa_onnx",
        llm_model="models/llm-7b/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",
        device="cpu",
        threads=min(4, os.cpu_count() or 1),
        llm_context=8192,
        chunk_chars=12000,
        ffmpeg=local_tool("ffmpeg"),
        ffprobe=local_tool("ffprobe"),
    )
    write_json(data_root / "settings.json", settings)
    return data_root / "settings.json"


def prepare(data_root, progress=lambda *a: None, cancelled=lambda: False):
    data_root = Path(data_root)
    data_root.mkdir(parents=True, exist_ok=True)
    for record in manifest():
        if cancelled():
            raise InterruptedError("Подготовка остановлена")
        download_one(record, data_root / "models", progress, cancelled)
    return configure(data_root)
