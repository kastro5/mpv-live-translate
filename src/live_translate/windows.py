"""Split a file's timeline into windows whose seams fall in silence.

Every seam depends only on the audio around its nominal boundary, so windows can be
processed in any order (seeks, parallel jobs) and still tile the timeline exactly.
"""

import re
from dataclasses import dataclass

SEAM_SEARCH_S = 8.0  # look this far either side of a nominal boundary for silence
MIN_SILENCE_S = 0.3
PAD_S = 1.5  # extra audio sent to Scribe on each side so words at a seam aren't clipped

_SILENCE_START = re.compile(r"silence_start: (-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end: (-?[\d.]+)")


@dataclass(frozen=True)
class Window:
    index: int
    start: float  # owned span [start, end)
    end: float


def nominal_boundaries(duration, window_s):
    """Interior boundaries every window_s seconds; a short tail joins the previous window."""
    count = max(1, round(duration / window_s))
    return [k * window_s for k in range(1, count) if k * window_s < duration]


def parse_silences(ffmpeg_stderr, offset, clip_len):
    """silencedetect output (times relative to the clip) -> absolute (start, end) pairs."""
    silences, start = [], None
    for line in ffmpeg_stderr.splitlines():
        if m := _SILENCE_START.search(line):
            start = max(0.0, float(m[1]))
        elif (m := _SILENCE_END.search(line)) and start is not None:
            silences.append((offset + start, offset + float(m[1])))
            start = None
    if start is not None:  # silence ran to the end of the clip
        silences.append((offset + start, offset + clip_len))
    return silences


def pick_seam(boundary, silences, search=SEAM_SEARCH_S):
    """Midpoint of the longest silence near the boundary, or the boundary itself."""
    lo, hi = boundary - search, boundary + search
    best, best_len = boundary, MIN_SILENCE_S
    for start, end in silences:
        start, end = max(start, lo), min(end, hi)
        if end - start >= best_len:
            best, best_len = (start + end) / 2, end - start
    return best


def make_windows(seams, duration):
    """Interior seams (sorted) -> windows tiling [0, duration)."""
    edges = [0.0, *seams, duration]
    return [Window(i, edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


def window_at(windows, t):
    for w in windows:
        if t < w.end:
            return w
    return windows[-1]


def audio_span(window, duration, pad=PAD_S):
    return max(0.0, window.start - pad), min(duration, window.end + pad)


def owned_words(window, words):
    """Tokens of a padded transcript that start inside the window's owned span."""
    return [w for w in words if window.start <= w["start"] < window.end]
