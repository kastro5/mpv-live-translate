"""Where subtitle files go, and writing them without mpv ever reading half a file."""

import os
import time
from pathlib import Path

from .stream import is_url


def choose_path(video, lang, cache, name=None):
    """movie.<lang>.srt next to the video (auto-loads in mpv), unless that file isn't ours
    or the folder is read-only; then the cache dir. A link has no folder: always the cache dir."""
    if is_url(video):
        return cache.dir / f"{Path(name).stem}.{lang}.srt"
    video = Path(video)
    beside = video.with_name(f"{video.stem}.{lang}.srt")
    if beside.exists():
        try:
            ours = cache.owns(beside, beside.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            ours = False
        if ours:
            return beside
    elif os.access(video.parent, os.W_OK):
        return beside
    return cache.dir / beside.name


def write_atomic(path, text, retries=10):
    """Temp file + os.replace. On Windows, antivirus or sync tools can briefly lock the target."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(retries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(0.2)
