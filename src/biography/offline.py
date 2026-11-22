import os
import socket
from contextlib import contextmanager
from unittest.mock import patch


@contextmanager
def offline_mode():
    """Process-wide network guard for this single-threaded CLI's model stage.

    Local ffmpeg is separately restricted to file/pipe protocols. This is a
    fail-closed Python guard, not an OS sandbox for malicious native libraries.
    """
    names = {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
    }

    def deny(*args, **kwargs):
        raise RuntimeError("Сеть отключена: используйте полные локальные модели")

    with (
        patch.dict(os.environ, names),
        patch.object(socket.socket, "connect", deny),
        patch.object(socket.socket, "connect_ex", deny),
        patch.object(socket, "create_connection", deny),
    ):
        yield
