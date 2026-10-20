"""Speech to text. Scribe v2 is the only real provider; FakeTranscriber is for tests and dry runs."""

import asyncio
from dataclasses import dataclass
from typing import Protocol

from elevenlabs.client import AsyncElevenLabs

SCRIBE_MODEL = "scribe_v2"
SCRIBE_PRICE_PER_HOUR = 0.22  # $ list price, used only for the cost estimate on screen
_KEEP = ("text", "start", "end", "type")


@dataclass
class Transcript:
    language_code: str
    language_probability: float
    words: list  # tokens {text, start, end, type: word|spacing|audio_event}, absolute times


class Transcriber(Protocol):
    model: str

    async def transcribe(self, flac: bytes, offset: float, length: float, language: str | None) -> Transcript: ...

    def cost(self, seconds: float) -> float: ...


class ScribeTranscriber:
    model = SCRIBE_MODEL

    def __init__(self, api_key):
        # Default 240 s timeout is too short for long files
        self.client = AsyncElevenLabs(api_key=api_key, timeout=900)

    async def transcribe(self, flac, offset, length, language=None):
        result = await self.client.speech_to_text.convert(
            file=("audio.flac", flac, "audio/flac"),
            model_id=self.model,
            language_code=language,
        )
        words = []
        for tok in result.words or []:
            tok = {k: getattr(tok, k) for k in _KEEP}
            if tok["start"] is None:
                continue
            tok["start"] += offset
            tok["end"] = (tok["end"] if tok["end"] is not None else tok["start"]) + offset
            words.append(tok)
        return Transcript(result.language_code, result.language_probability or 0.0, words)

    def cost(self, seconds):
        return seconds / 3600 * SCRIBE_PRICE_PER_HOUR


class FakeTranscriber:
    """One numbered 'word' per second of audio, so pipelines can run without API keys."""

    model = "fake"

    def __init__(self, language="jpn", delay=0.0):
        self.language, self.delay = language, delay

    async def transcribe(self, flac, offset, length, language=None):
        if self.delay:
            await asyncio.sleep(self.delay)
        words = []
        for i in range(int(length)):
            t = offset + i + 0.1
            words.append({"text": f"w{int(t)}", "start": t, "end": t + 0.5, "type": "word"})
            words.append({"text": " ", "start": t + 0.5, "end": t + 0.6, "type": "spacing"})
            if i % 4 == 3:
                words[-2]["text"] += "."
        return Transcript(language or self.language, 1.0, words)

    def cost(self, seconds):
        return 0.0
