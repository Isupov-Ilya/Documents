"""Stable frozen entry point, including explicit isolated model workers."""

import multiprocessing
import os
import runpy
import sys


def main():
    multiprocessing.freeze_support()
    os.environ["PYTHONUTF8"] = "1"
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 2 and sys.argv[1] == "--internal-worker":
        kind = sys.argv[2]
        modules = {
            "diarization": "biography.sherpa_backend",
            "asr": "biography.asr_worker",
        }
        if kind not in modules:
            raise SystemExit("Unknown worker")
        sys.argv = [sys.argv[0], *sys.argv[3:]]
        runpy.run_module(modules[kind], run_name="__main__")
        return
    if len(sys.argv) > 1 and sys.argv[1] != "--gui":
        from biography.cli import main as cli

        raise SystemExit(cli())
    if sys.platform == "win32":
        import ctypes

        processes = (ctypes.c_ulong * 16)()
        if ctypes.windll.kernel32.GetConsoleProcessList(processes, 16) == 1:
            ctypes.windll.user32.ShowWindow(
                ctypes.windll.kernel32.GetConsoleWindow(), 0
            )
    from biography.gui import main as gui

    raise SystemExit(gui())


if __name__ == "__main__":
    main()
