"""Runs the pipeline for one video: seams -> audio cut -> Scribe -> cues -> Claude -> .srt files.

The same Session backs `generate` (whole file, from 0) and `serve` (follows the mpv playhead).
"""

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

import anthropic

from . import langs, scheduler
from .cache import Cache, file_key
from .cues import build_cues, format_srt, join_cues, layout_for
from .output import choose_path, write_atomic
from .translate import PROMPT_VERSION, TranslationError
from .windows import SEAM_SEARCH_S, Window, audio_span, nominal_boundaries, owned_words, pick_seam

WINDOW_SLOTS = 3  # windows in flight at once
SCRIBE_SLOTS = 2
CLAUDE_SLOTS = 2
MAX_ATTEMPTS = 3
CONTEXT_CUES = 20  # cues of the previous window shown to Claude for continuity
STOP_GRACE_S = 2.0  # let in-flight (already paid) requests land in the cache before exiting
LOCK_PROBABILITY = 0.8
LOCK_MIN_WORDS = 20


class FatalError(Exception):
    """Something no retry will fix, like a rejected API key."""


@dataclass
class WindowResult:
    window: Window
    language: str
    cues: list  # (start, end, source text)
    texts: list | None  # one translation per cue; None if translation failed


def _is_fatal(exc):
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return True
    return getattr(exc, "status_code", None) in (401, 403)  # elevenlabs ApiError


class Session:
    def __init__(self, video, config, transcriber, translator, ffmpeg, cache_root, on_update=None, on_message=None):
        self.video = Path(video)
        self.config = config
        self.transcriber, self.translator, self.ffmpeg = transcriber, translator, ffmpeg
        self.cache_root = cache_root
        self.on_update = on_update or (lambda session: None)
        self.on_message = on_message or (lambda text: None)

        self.position = 0.0
        self.cost = 0.0
        self.results = {}
        self.failed = set()
        self.outputs = {}  # "translation" / "source" -> path
        self._attempts, self._retry_at = {}, {}
        self._seams = {}  # boundary -> task, so two windows never detect the same seam twice
        self._wake = asyncio.Event()
        self._stopping = False
        self._fatal = None
        self._scribe = asyncio.Semaphore(SCRIBE_SLOTS)
        self._claude = asyncio.Semaphore(CLAUDE_SLOTS)

    async def prepare(self, duration=None):
        self.duration = duration or await self.ffmpeg.duration(self.video)
        self.cache = Cache(self.cache_root, file_key(self.video))
        self.boundaries = nominal_boundaries(self.duration, self.config.window_s)
        self.starts = [0.0, *self.boundaries]
        source = self.config.source_language
        self.language = langs.iso3(source) if source != "auto" else self.cache.meta.get("language")
        self.target = langs.iso1(self.config.target)
        self.variant = f"{self.target}/{self.translator.model}-{self.translator.effort}-p{PROMPT_VERSION}"
        for i in range(len(self.starts)):
            self._load_cached(i)
        if self.results:
            self._write_outputs()

    # --- controls -------------------------------------------------------------------------

    def set_position(self, t):
        self.position = max(0.0, t)
        self._wake.set()

    def stop(self):
        self._stopping = True
        self._wake.set()

    def ready_until(self):
        """End of the finished stretch starting at the playhead, or None if the current window isn't done."""
        i = scheduler.window_index(self.starts, self.position)
        if i not in self.results:
            return None
        while i + 1 in self.results:
            i += 1
        return self.results[i].window.end

    # --- main loop ------------------------------------------------------------------------

    async def run(self, until_complete=False):
        running = {}
        while not self._stopping:
            now = time.monotonic()
            waiting = {i for i, at in self._retry_at.items() if at > now}
            order = scheduler.wanted(
                self.starts, self.position, self.config.mode, self.config.lookahead_s,
                set(self.results) | self.failed | waiting,
            )
            if until_complete and not order and not running and not waiting:
                break
            for i in scheduler.to_start(order, running, WINDOW_SLOTS):
                running[i] = asyncio.create_task(self._process(i))

            wake = asyncio.create_task(self._wake.wait())
            await asyncio.wait([*running.values(), wake], timeout=1.0, return_when=asyncio.FIRST_COMPLETED)
            wake.cancel()
            self._wake.clear()
            for i, task in list(running.items()):
                if task.done():
                    del running[i]
                    self._finished(i, task)

        if running:
            await asyncio.wait(running.values(), timeout=STOP_GRACE_S)
            for task in running.values():
                task.cancel()
        if self._fatal:
            raise self._fatal

    def _finished(self, i, task):
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            self._attempts.pop(i, None)
            self._retry_at.pop(i, None)
            return
        if _is_fatal(exc):
            self._fatal = FatalError(str(exc))
            self.stop()
            return
        self._attempts[i] = self._attempts.get(i, 0) + 1
        if self._attempts[i] >= MAX_ATTEMPTS:
            self.failed.add(i)
            self.on_message(f"Window {i + 1} failed: {exc}")
        else:
            self._retry_at[i] = time.monotonic() + 5 * self._attempts[i]

    # --- one window -----------------------------------------------------------------------

    async def _seam(self, b):
        """Seam near boundary index b."""
        boundary = self.boundaries[b]
        cached = self.cache.seam(self.config.window_s, boundary)
        if cached is not None:
            return cached
        if boundary not in self._seams:
            self._seams[boundary] = asyncio.create_task(self._detect_seam(boundary))
        return await self._seams[boundary]

    async def _detect_seam(self, boundary):
        search = min(SEAM_SEARCH_S, self.config.window_s / 3)
        lo, hi = max(0.0, boundary - search), min(self.duration, boundary + search)
        silences = await self.ffmpeg.silences(self.video, lo, hi - lo)
        seam = pick_seam(boundary, silences, search)
        self.cache.set_seam(self.config.window_s, boundary, seam)
        return seam

    async def _window(self, i):
        start = 0.0 if i == 0 else await self._seam(i - 1)
        end = self.duration if i == len(self.starts) - 1 else await self._seam(i)
        return Window(i, start, end)

    def _known_window(self, i):
        """The window if its seams are already cached, else None."""
        start = 0.0 if i == 0 else self.cache.seam(self.config.window_s, self.boundaries[i - 1])
        last = i == len(self.starts) - 1
        end = self.duration if last else self.cache.seam(self.config.window_s, self.boundaries[i])
        return None if start is None or end is None else Window(i, start, end)

    def _load_cached(self, i):
        window = self._known_window(i)
        if not window:
            return
        transcript = self.cache.transcript(self.transcriber.model, window.start, window.end)
        if transcript is None:
            return
        cues = build_cues(transcript["words"], layout_for(transcript["language_code"]))
        texts = self._cached_texts(transcript["language_code"], cues, window)
        if texts is not None:
            self.results[i] = WindowResult(window, transcript["language_code"], cues, texts)

    def _cached_texts(self, language, cues, window):
        if not cues:
            return []
        if langs.iso1(language) == self.target:
            return [text for _, _, text in cues]
        texts = self.cache.translation(self.variant, window.start, window.end)
        return texts if texts is not None and len(texts) == len(cues) else None

    async def _process(self, i):
        window = await self._window(i)
        transcript = self.cache.transcript(self.transcriber.model, window.start, window.end)
        if transcript is None:
            a, b = audio_span(window, self.duration)
            async with self._scribe:
                flac = await self.ffmpeg.flac(self.video, a, b - a)
                result = await self.transcriber.transcribe(flac, a, b - a, self.language)
            self.cost += self.transcriber.cost(b - a)
            transcript = {
                "language_code": result.language_code,
                "language_probability": result.language_probability,
                "words": owned_words(window, result.words),
            }
            self.cache.set_transcript(self.transcriber.model, window.start, window.end, transcript)
        self._maybe_lock_language(transcript)

        language = transcript["language_code"]
        cues = build_cues(transcript["words"], layout_for(language))
        texts = self._cached_texts(language, cues, window)
        if texts is None:
            try:
                async with self._claude:
                    texts, usage = await self.translator.translate(
                        cues, self._context(i), langs.name(language), langs.name(self.target)
                    )
                self.cost += usage.cost(self.translator.model)
                self.cache.set_translation(self.variant, window.start, window.end, texts)
            except TranslationError as e:
                # A refusal won't change on retry: keep the original-language cues, skip the translation
                self.cost += e.usage.cost(self.translator.model) if hasattr(e, "usage") else 0.0
                self.failed.add(i)
                self.on_message(f"Window {i + 1} not translated: {e}")
                texts = None

        self.results[i] = WindowResult(window, language, cues, texts)
        self._write_outputs()

    def _maybe_lock_language(self, transcript):
        if self.language or self.config.source_language != "auto":
            return
        words = sum(1 for w in transcript["words"] if w["type"] == "word")
        if transcript["language_probability"] > LOCK_PROBABILITY and words >= LOCK_MIN_WORDS:
            self.language = transcript["language_code"]
            self.cache.meta["language"] = self.language
            self.cache.save_meta()

    def _context(self, i):
        prev = self.results.get(i - 1)
        if not prev or not prev.cues:
            return []
        texts = prev.texts or [None] * len(prev.cues)
        return list(zip(prev.cues, texts))[-CONTEXT_CUES:]

    # --- output ---------------------------------------------------------------------------

    def _write_outputs(self):
        done = [self.results[i] for i in sorted(self.results)]
        source_lang = langs.iso1(self.language or done[0].language)
        if not self.outputs:
            self.outputs = {"translation": choose_path(self.video, self.target, self.cache)}
            if source_lang != self.target:
                self.outputs["source"] = choose_path(self.video, source_lang, self.cache)

        source = join_cues(r.cues for r in done)
        texts = [t for r in done for t in (r.texts or [None] * len(r.cues))]
        translated = [(s, e, t) for (s, e, _), t in zip(source, texts) if t]
        files = {"translation": (translated, layout_for(langs.iso3(self.target)))}
        if "source" in self.outputs:
            files["source"] = (source, layout_for(langs.iso3(source_lang)))
        for kind, (cues, layout) in files.items():
            text = format_srt(cues, layout.line_chars)
            write_atomic(self.outputs[kind], text)
            self.cache.record_output(self.outputs[kind], text)
        self.on_update(self)
