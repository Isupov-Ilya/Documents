import json
import sys
from pathlib import Path

from .backends import _transcribe_worker
from .offline import offline_mode
from .schemas import Settings

if __name__ == "__main__":
    from .runtime import configure_stdio

    configure_stdio()
    with offline_mode():
        settings = Settings.model_validate_json(sys.stdin.read())
        result = _transcribe_worker(Path(sys.argv[1]), settings)
        print(
            json.dumps([s.model_dump(mode="json") for s in result], ensure_ascii=False)
        )
