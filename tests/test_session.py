import asyncio
import dataclasses

import pytest

from live_translate.config import Config
from live_translate.session import FatalError, Session
from live_translate.transcribe import FakeTranscriber
from live_translate.translate import FakeTranslator, TranslationError, Usage


class FakeFfmpeg:
    """Silence exactly at every 60 s boundary + 1 s, so seams are predictable."""

    def __init__(self, duration):
        self._duration = duration
        self.silence_calls = 0

    async def duration(self, path):
        return self._duration

    async def silences(self, path, start, length):
        self.silence_calls += 1
        return [(t + 0.5, t + 1.5) for t in range(60, int(self._duration), 60) if start <= t + 1 < start + length]

    async def flac(self, path, start, length):
        return b""


def make_session(tmp_path, duration=200.0, translator=None, **config):
    video = tmp_path / "movie.mkv"
    video.write_bytes(b"x" * 100)
    conf = dataclasses.replace(Config(), **config)
    ffmpeg = FakeFfmpeg(duration)
    session = Session(video, conf, FakeTranscriber(), translator or FakeTranslator(), ffmpeg, tmp_path / "cache")
    return session, ffmpeg


def run(coro):
    return asyncio.run(coro)


def test_generate_whole_file_writes_both_tracks(tmp_path):
    session, _ = make_session(tmp_path, mode="full")

    async def go():
        await session.prepare()
        await session.run(until_complete=True)

    run(go())
    assert sorted(session.results) == [0, 1, 2]
    assert [r.window.start for r in session.results.values()] == [0.0, 61.0, 121.0]
    en = (tmp_path / "movie.en.srt").read_text()
    ja = (tmp_path / "movie.ja.srt").read_text()
    assert "[English] w0" in en and "w198" in ja


def test_second_run_is_fully_cached(tmp_path):
    session, _ = make_session(tmp_path, mode="full")

    async def go(s):
        await s.prepare()
        await s.run(until_complete=True)

    run(go(session))
    again, ffmpeg = make_session(tmp_path, mode="full")
    run(again.prepare())
    assert sorted(again.results) == [0, 1, 2] and ffmpeg.silence_calls == 0


def test_lookahead_stops_at_horizon(tmp_path):
    session, _ = make_session(tmp_path, duration=600.0, mode="lookahead", lookahead_s=120.0)

    async def go():
        await session.prepare()
        session.set_position(130.0)
        task = asyncio.create_task(session.run())
        for _ in range(100):
            await asyncio.sleep(0.02)
            if len(session.results) == 3:
                break
        await asyncio.sleep(0.1)  # nothing more should start
        session.stop()
        await task

    run(go())
    assert sorted(session.results) == [2, 3, 4]
    assert session.ready_until() == 301.0


def test_refusal_keeps_source_and_skips_translation(tmp_path):
    class Refusing(FakeTranslator):
        async def translate(self, cues, context, source, target):
            if cues[0][0] > 60:
                e = TranslationError("declined")
                e.usage = Usage()
                raise e
            return await super().translate(cues, context, source, target)

    session, _ = make_session(tmp_path, translator=Refusing(), mode="full")
    messages = []
    session.on_message = messages.append

    async def go():
        await session.prepare()
        await session.run(until_complete=True)

    run(go())
    en = (tmp_path / "movie.en.srt").read_text()
    ja = (tmp_path / "movie.ja.srt").read_text()
    assert "[English] w0" in en and "w70" not in en and "w70" in ja
    assert any("not translated" in m for m in messages)


def test_rejected_key_stops_the_session(tmp_path):
    class BadKey(FakeTranslator):
        async def translate(self, cues, context, source, target):
            e = Exception("unauthorized")
            e.status_code = 401
            raise e

    session, _ = make_session(tmp_path, translator=BadKey(), mode="full")

    async def go():
        await session.prepare()
        await session.run(until_complete=True)

    with pytest.raises(FatalError):
        run(go())


def test_debug_events_cover_each_stage(tmp_path):
    from live_translate.debuglog import DebugLog, panel_lines

    log = DebugLog(keep=100)
    session, _ = make_session(tmp_path, mode="full")
    session.debug = log

    async def go():
        await session.prepare()
        await session.run(until_complete=True)

    run(go())
    text = "\n".join(log.events)
    for expected in ("seam near", "ffmpeg cut", "Scribe done", "Claude start", "Claude done", "wrote", "language locked"):
        assert expected in text, expected
    assert "done 1-3" in "\n".join(panel_lines(session, log))
