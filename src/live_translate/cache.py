"""On-disk cache of seams, transcripts and translations per video, so rewatching costs nothing."""

import hashlib
import json
import os
from pathlib import Path

_SAMPLE = 1 << 20  # bytes hashed from each end of the file


def file_key(path):
    """Identity of a video that survives renames and moves: size plus its first and last MB."""
    path = Path(path)
    size = path.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(_SAMPLE))
        if size > _SAMPLE:
            f.seek(max(_SAMPLE, size - _SAMPLE))
            h.update(f.read(_SAMPLE))
    return h.hexdigest()[:20]


def _span(start, end):
    return f"{start:.3f}-{end:.3f}"


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


class Cache:
    def __init__(self, root, key):
        self.dir = Path(root) / key
        self._meta_path = self.dir / "meta.json"
        self.meta = read_json(self._meta_path) or {}

    def save_meta(self):
        write_json(self._meta_path, self.meta)

    # Seams are keyed by window size and nominal boundary
    def seam(self, window_s, boundary):
        return self.meta.get("seams", {}).get(f"{window_s:g}", {}).get(f"{boundary:.3f}")

    def set_seam(self, window_s, boundary, seam):
        self.meta.setdefault("seams", {}).setdefault(f"{window_s:g}", {})[f"{boundary:.3f}"] = seam
        self.save_meta()

    def _transcript_path(self, model, start, end):
        return self.dir / "transcripts" / model / f"{_span(start, end)}.json"

    def transcript(self, model, start, end):
        return read_json(self._transcript_path(model, start, end))

    def set_transcript(self, model, start, end, data):
        write_json(self._transcript_path(model, start, end), data)

    def _translation_path(self, variant, start, end):
        return self.dir / "translations" / variant / f"{_span(start, end)}.json"

    def translation(self, variant, start, end):
        return read_json(self._translation_path(variant, start, end))

    def set_translation(self, variant, start, end, texts):
        write_json(self._translation_path(variant, start, end), texts)

    # Subtitle files we wrote next to videos, by content hash, so we never overwrite someone else's
    def owns(self, path, text_on_disk):
        recorded = self.meta.get("outputs", {}).get(str(path))
        return recorded == hashlib.sha256(text_on_disk.encode("utf-8")).hexdigest()

    def record_output(self, path, text):
        self.meta.setdefault("outputs", {})[str(path)] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.save_meta()
