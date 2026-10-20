"""Transcribe an audio file with ElevenLabs Scribe v2.

Usage: uv run scribe.py audio/clip.mp3 [--lang spa] [--diarize] [--events]
"""

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs

load_dotenv()

OUT_DIR = Path(__file__).parent / "outputs" / "elevenlabs_scribe_v2"


def transcribe(audio, lang=None, diarize=False, events=False):
    """Run Scribe v2 on an audio file, save <stem>.json/.txt to OUT_DIR, return the response dict."""
    # Default 240s timeout is too short for long files
    client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"], timeout=900)

    start = time.perf_counter()
    with audio.open("rb") as f:
        result = client.speech_to_text.convert(
            file=f,
            model_id="scribe_v2",
            language_code=lang,
            diarize=diarize,
            tag_audio_events=events,
        )
    elapsed = time.perf_counter() - start

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = result.model_dump()
    (OUT_DIR / f"{audio.stem}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))
    (OUT_DIR / f"{audio.stem}.txt").write_text(result.text)

    print(f"language: {result.language_code} ({result.language_probability:.2f})  |  {elapsed:.1f}s")
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--lang", default=None, help="ISO-639 code; omit to auto-detect")
    parser.add_argument("--diarize", action="store_true")
    parser.add_argument("--events", action="store_true", help="tag laughter, applause, etc.")
    args = parser.parse_args()

    data = transcribe(args.audio, args.lang, args.diarize, args.events)
    print(data["text"])


if __name__ == "__main__":
    main()
