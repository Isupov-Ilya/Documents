import os
import sys
from pathlib import Path


def process_command(kind=None, *arguments):
    if getattr(sys, "frozen", False):
        return [
            sys.executable,
            *(["--internal-worker", kind] if kind else []),
            *arguments,
        ]
    modules = {"asr": "biography.asr_worker", "diarization": "biography.sherpa_backend"}
    return [sys.executable, "-m", modules[kind] if kind else "biography", *arguments]


def data_directory():
    override = os.environ.get("BIOGRAPHY_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Biography"
    return Path.home() / ".local" / "share" / "Biography"


def application_directory():
    return (
        Path(sys.executable).parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[2]
    )


def local_tool(name):
    filename = name + (".exe" if sys.platform == "win32" else "")
    path = application_directory() / "tools" / filename
    return str(path) if path.is_file() else name


def resource_path(relative):
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return root / relative


def configure_stdio():
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
