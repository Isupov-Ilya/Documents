"""Build on native Windows. Linux cannot cross-build a Windows PyInstaller app."""

import importlib.metadata
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    if sys.platform != "win32":
        raise SystemExit("Run this build on Windows x64")
    root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",
        "--name",
        "Biography",
        "--paths",
        str(root / "src"),
    ]
    for package in [
        "faster_whisper",
        "ctranslate2",
        "tokenizers",
        "sherpa_onnx",
        "sherpa_onnx_core",
        "llama_cpp",
        "pydantic_core",
        "av",
        "onnxruntime",
    ]:
        command.extend(["--collect-all", package])
    for module in [
        "biography.cli",
        "biography.gui",
        "biography.sherpa_backend",
        "biography.asr_worker",
    ]:
        command.extend(["--hidden-import", module])
    command.extend(
        [
            "--collect-data",
            "biography",
            "--hidden-import",
            "onnx",
            "--add-data",
            str(root / "examples" / "audio") + ";examples/audio",
        ]
    )
    command.append(str(root / "packaging" / "windows_entry.py"))
    subprocess.run(command, cwd=root, check=True)
    destination = root / "dist" / "Biography" / "tools"
    destination.mkdir(exist_ok=True)
    unpack = root / "build" / "windows-tools" / "unpacked"
    source = next(unpack.rglob("ffmpeg.exe")).parent
    for file in source.iterdir():
        if file.suffix.lower() in {".exe", ".dll"} and file.name != "ffplay.exe":
            shutil.copy2(file, destination / file.name)
    notices = root / "dist" / "Biography" / "THIRD-PARTY"
    notices.mkdir(exist_ok=True)
    shutil.copy2(
        root / "build" / "windows-tools" / "source.json", notices / "ffmpeg-source.json"
    )
    for file in unpack.rglob("*"):
        if file.is_file() and (
            "LICENSE" in file.name.upper() or "COPYING" in file.name.upper()
        ):
            shutil.copy2(file, notices / file.name)
    # Retain supplied licenses for redistributed Python/Qt components.
    for distribution in importlib.metadata.distributions():
        package = distribution.metadata.get("Name", "package").replace("/", "_")
        for entry in distribution.files or []:
            if entry.name.upper().startswith(("LICENSE", "COPYING")):
                source = Path(distribution.locate_file(entry))
                if source.is_file():
                    folder = notices / package
                    folder.mkdir(exist_ok=True)
                    shutil.copy2(source, folder / entry.name)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, notices / "Python-LICENSE.txt")
    redistributable = Path(os.environ.get("VCToolsRedistDir", "C:/missing"))
    crt = list(redistributable.glob("x64/Microsoft.VC*.CRT"))
    if crt:
        for dll in crt[-1].glob("*.dll"):
            shutil.copy2(dll, root / "dist" / "Biography" / dll.name)
    (notices / "NOTICE.txt").write_text(
        "FFmpeg LGPL shared build: see ffmpeg-source.json for verified binary provenance and build source.\nPython packages and model licenses retain their original terms.\nModel LICENSE/README files are downloaded alongside the models.\nPySide6/Qt is dynamically linked under LGPLv3; https://code.qt.io/ and https://download.qt.io/archive/qt/6.8/6.8.3/ for corresponding sources.\nWeSpeaker model: CC-BY-4.0, https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM ; metadata-only derivative.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
