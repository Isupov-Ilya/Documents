"""Obtain a publisher-checksummed LGPL shared FFmpeg build for the Windows bundle."""

import hashlib
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "build" / "windows-tools"
    output.mkdir(parents=True, exist_ok=True)
    url = "https://api.github.com/repos/FFmpeg-Builds/FFmpeg-Builds/releases/latest"
    with urllib.request.urlopen(url, timeout=60) as response:
        release = json.load(response)
    asset = next(
        a
        for a in release["assets"]
        if a["name"] == "ffmpeg-master-latest-win64-lgpl-shared.zip"
    )
    expected = asset.get("digest", "")
    if not expected.startswith("sha256:"):
        raise ValueError("Publisher SHA-256 is required")
    archive = output / "ffmpeg.zip"
    if (
        not archive.exists()
        or hashlib.sha256(archive.read_bytes()).hexdigest() != expected.split(":")[1]
    ):
        with (
            urllib.request.urlopen(
                asset["browser_download_url"], timeout=120
            ) as response,
            archive.open("wb") as stream,
        ):
            shutil.copyfileobj(response, stream)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected.split(":")[1]:
        raise ValueError("FFmpeg publisher checksum mismatch")
    unpack = output / "unpacked"
    unpack.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        if sum(i.file_size for i in z.infolist()) > 1024**3:
            raise ValueError("Unexpected archive size")
        for item in z.infolist():
            if not (unpack / item.filename).resolve().is_relative_to(unpack.resolve()):
                raise ValueError("Unsafe archive member")
        z.extractall(unpack)
    binary = next(unpack.rglob("ffmpeg.exe")).parent
    (output / "source.json").write_text(
        json.dumps(
            {
                "url": asset["browser_download_url"],
                "publisher_sha256": expected,
                "release_id": release["id"],
                "published_at": release["published_at"],
                "license": "LGPL shared build",
                "build_sources": "https://github.com/FFmpeg-Builds/FFmpeg-Builds",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(binary)


if __name__ == "__main__":
    main()
