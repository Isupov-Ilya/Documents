"""Content-checked stage reuse. A new run never overwrites its predecessor."""

import hashlib
import json
import marshal
import shutil
from pathlib import Path

from .io import sha256, write_json


def fingerprint(path):
    path = Path(path)
    if path.is_file():
        return sha256(path)
    if path.is_dir():
        return digest(
            {
                str(p.relative_to(path)): sha256(p)
                for p in sorted(path.rglob("*"))
                if p.is_file()
            }
        )
    return "missing:" + str(path)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def code_fingerprint(function):
    return hashlib.sha256(marshal.dumps(function.__code__)).hexdigest()


class Checkpoints:
    def __init__(self, output, previous=None):
        self.output = Path(output)
        self.previous = Path(previous) if previous else None
        self.records = {}
        self.old = {}
        if self.previous and (self.previous / "checkpoints.json").is_file():
            try:
                self.old = json.loads(
                    (self.previous / "checkpoints.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                pass

    def restore(self, stage, key, names):
        record = self.old.get(stage, {})
        if record.get("key") != key or not self.previous:
            return False
        for name in names:
            path = self.previous / name
            if not path.resolve().is_relative_to(self.previous.resolve()):
                return False
            if not path.is_file() or record.get("artifacts", {}).get(name) != sha256(
                path
            ):
                return False
        for name in names:
            shutil.copy2(self.previous / name, self.output / name)
        self.record(stage, key, names)
        return True

    def record(self, stage, key, names):
        self.records[stage] = {
            "key": key,
            "artifacts": {name: sha256(self.output / name) for name in names},
        }
        write_json(self.output / "checkpoints.json", self.records)
