"""Video -> transcript (Scribe v2) -> subtitles in the source language + English per Claude model.

Usage: uv run subtitle.py path/to/video.mp4 [--lang jpn] [--models claude-opus-5-5,claude-haiku-5-5]

Reuses outputs/elevenlabs_scribe_v2/<stem>.json if present, so reruns only pay for translation.
"""

import argparse
import csv
import json
import subprocess
import time
from pathlib import Path

from scribe import OUT_DIR, transcribe
from srt import LATIN, build_cues, layout_for, timestamp, write_srt
from translate import TranslationError, translate_cues

AUDIO_DIR = Path(__file__).parent / "audio"
AUDIO_EXTS = {".m4a", ".mp3", ".flac", ".wav", ".ogg", ".opus", ".aac"}
LANG_NAMES = {"jpn": "Japanese", "zho": "Chinese", "kor": "Korean", "por": "Portuguese", "spa": "Spanish"}
ISO1_TO_3 = {"ja": "jpn", "zh": "zho", "ko": "kor", "pt": "por", "es": "spa", "en": "eng"}  # Scribe reports 3-letter codes


def extract_audio(video):
    """Pull the audio track out of a video into AUDIO_DIR; audio files pass through."""
    if video.suffix.lower() in AUDIO_EXTS:
        return video
    AUDIO_DIR.mkdir(exist_ok=True)
    out = AUDIO_DIR / f"{video.stem}.m4a"
    if out.exists():
        return out
    # Stream copy works for AAC (most MP4s); otherwise re-encode to mono 16 kHz FLAC
    copy = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vn", "-c:a", "copy", str(out)])
    if copy.returncode == 0:
        return out
    out.unlink(missing_ok=True)
    out = AUDIO_DIR / f"{video.stem}.flac"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "flac", str(out)],
        check=True,
    )
    return out


def load_or_transcribe(audio, lang):
    cached = OUT_DIR / f"{audio.stem}.json"
    if cached.exists():
        data = json.loads(cached.read_text())
        if lang and data["language_code"] != ISO1_TO_3.get(lang, lang):
            raise SystemExit(
                f"{cached} is a {data['language_code']} transcript, not {lang}. Rename the video or delete it."
            )
        print(f"Reusing transcript {cached}")
        return data
    print(f"Transcribing {audio} with Scribe v2...")
    return transcribe(audio, lang)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("--lang", default="jpn", help="ISO-639 code of the spoken language")
    parser.add_argument("--models", default="claude-opus-5-5,claude-haiku-5-5")
    args = parser.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    audio = extract_audio(args.video)
    data = load_or_transcribe(audio, args.lang)
    src_lang = data["language_code"]
    layout = layout_for(src_lang)
    cues = build_cues(data["words"], layout)
    stem = audio.stem

    src_srt = OUT_DIR / f"{stem}.{src_lang}.srt"
    write_srt(cues, src_srt, layout.line_chars)
    print(f"{len(cues)} cues -> {src_srt}")

    results = {}
    for model in models:
        print(f"Translating with {model}...")
        start = time.perf_counter()
        try:
            english, usage = translate_cues(cues, model, LANG_NAMES.get(src_lang, src_lang))
        except TranslationError as e:
            print(f"  failed: {e}")
            continue
        elapsed = time.perf_counter() - start
        out = OUT_DIR / f"{stem}.en.{model}.srt"
        write_srt([(s, e, t) for (s, e, _), t in zip(cues, english)], out, LATIN.line_chars)
        results[model] = english
        print(
            f"  {usage.input_tokens:,} in / {usage.output_tokens:,} out tokens"
            f"  |  ${usage.cost(model):.4f}  |  {elapsed:.1f}s  ->  {out}"
        )

    if results:
        compare = OUT_DIR / f"{stem}.compare.tsv"
        with compare.open("w", newline="") as f:
            w = csv.writer(f, delimiter="\t")
            w.writerow(["time", src_lang, *results])
            for i, (start, _, text) in enumerate(cues):
                w.writerow([timestamp(start), text, *(r[i] for r in results.values())])
        print(f"Side-by-side -> {compare}")


if __name__ == "__main__":
    main()
