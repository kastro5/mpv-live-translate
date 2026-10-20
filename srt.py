"""Build an .srt subtitle file from a saved Scribe JSON response (no API call).

Usage: uv run srt.py outputs/elevenlabs_scribe_v2/test.json
"""

import argparse
import json
import textwrap
from dataclasses import dataclass
from pathlib import Path

MAX_LINES = 2
MAX_CUE_S = 6.0
SILENCE_GAP_S = 1.0  # a pause longer than this always starts a new cue
SENTENCE_END = (".", "?", "!", "…", "。", "？", "！")
MIN_CUE_S = 1.0


@dataclass(frozen=True)
class Layout:
    line_chars: int  # subtitle line width
    min_sentence_chars: int  # don't end a cue at a sentence boundary before this much text
    read_s_per_char: float  # caps cues where Scribe stretches one word across a long pause


LATIN = Layout(line_chars=42, min_sentence_chars=20, read_s_per_char=0.1)
# Japanese/Chinese: no spaces and more meaning per character. The reading-time cap is
# looser because translated English cues reuse these timings and need longer on screen.
CJK = Layout(line_chars=20, min_sentence_chars=8, read_s_per_char=0.25)
CJK_LANGS = {"jpn", "ja", "zho", "zh", "cmn", "yue"}


def layout_for(language_code):
    return CJK if language_code in CJK_LANGS else LATIN


def build_cues(words, layout=LATIN):
    """Group word tokens into cues of (start, end, text)."""
    cues, current = [], []

    def flush():
        if current:
            text = "".join(t["text"] for t in current).strip()
            words_only = [t for t in current if t["type"] == "word"]
            cues.append((words_only[0]["start"], words_only[-1]["end"], text))
            current.clear()

    for tok in words:
        if tok["type"] == "spacing":
            if current:
                current.append(tok)
            continue
        if tok["type"] != "word":  # skip audio events like (laughter)
            continue

        if current:
            first = next(t for t in current if t["type"] == "word")
            last = [t for t in current if t["type"] == "word"][-1]
            text_len = len("".join(t["text"] for t in current).strip()) + 1 + len(tok["text"])
            if (
                tok["start"] - last["end"] > SILENCE_GAP_S
                or tok["end"] - first["start"] > MAX_CUE_S
                or text_len > layout.line_chars * MAX_LINES
            ):
                flush()

        current.append(tok)
        # End cues at sentence boundaries once there's enough text to read
        if (
            tok["text"].endswith(SENTENCE_END)
            and len("".join(t["text"] for t in current)) > layout.min_sentence_chars
        ):
            flush()

    flush()
    return fix_durations(cues, layout)


def fix_durations(cues, layout=LATIN):
    """Cap overly long cues by reading time and extend too-short ones, without overlapping."""
    fixed = []
    for i, (start, end, text) in enumerate(cues):
        end = min(end, start + 1.0 + len(text) * layout.read_s_per_char)
        next_start = cues[i + 1][0] if i + 1 < len(cues) else float("inf")
        end = max(end, min(start + MIN_CUE_S, next_start))
        fixed.append((start, end, text))
    return fixed


def timestamp(seconds):
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def write_srt(cues, path, line_chars=LATIN.line_chars):
    blocks = []
    for i, (start, end, text) in enumerate(cues, 1):
        lines = "\n".join(textwrap.wrap(text, line_chars))
        blocks.append(f"{i}\n{timestamp(start)} --> {timestamp(end)}\n{lines}\n")
    path.write_text("\n".join(blocks))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("json_file", type=Path)
    args = parser.parse_args()

    data = json.loads(args.json_file.read_text())
    layout = layout_for(data.get("language_code"))
    cues = build_cues(data["words"], layout)

    out = args.json_file.with_suffix(".srt")
    write_srt(cues, out, layout.line_chars)
    print(f"{len(cues)} cues -> {out}")


if __name__ == "__main__":
    main()
