"""`live-translate serve`: follow an mpv player and keep its subtitle tracks up to date."""

import asyncio
import os
import time

from python_mpv_jsonipc import MPVError

from . import config as cfg
from . import langs
from .audio import Ffmpeg
from .debuglog import DebugLog, ass_panel, panel_lines
from .mpv_ipc import Mpv, MpvGone
from .session import Session

POLL_S = 1.0
STOP_MESSAGE = "live-translate-stop"
DEBUG_MESSAGE = "live-translate-debug"  # toggles the debug panel
OVERLAY_ID = 47  # any id unique among this client's osd overlays


def _same_file(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _clock(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


class Tracks:
    """Our external subtitle tracks in mpv. Replacing a file means sub-add + sub-remove
    (sub-reload would force the track to primary and drop its title), then putting the
    user's sid / secondary-sid choice back."""

    def __init__(self, mpv, debug):
        self.mpv = mpv
        self.debug = debug
        self.ids = {}
        self._dirty = asyncio.Event()
        self._session = None

    def request_update(self, session):
        self._session = session
        self._dirty.set()

    def pending(self):
        return self._dirty.is_set() and self._session is not None

    async def run(self):
        while True:
            await self._dirty.wait()
            self._dirty.clear()
            try:
                await self._sync(self._session)
            except MPVError:
                pass  # mpv refused a command (e.g. file just closed); the next update retries

    async def _find(self, path):
        ids = [
            t["id"] for t in await self.mpv.get("track-list", [])
            if t.get("type") == "sub" and t.get("external-filename") and _same_file(t["external-filename"], path)
        ]
        return max(ids) if ids else None

    async def _sync(self, session):
        for kind, path in session.outputs.items():
            lang = session.target if kind == "translation" else langs.iso1(session.language or "")
            old = self.ids.get(kind) or await self._find(path)  # mpv may have auto-loaded our file
            sid, secondary = await self.mpv.get("sid"), await self.mpv.get("secondary-sid")
            first_time = old is None and kind not in self.ids
            flag = "select" if kind == "translation" and first_time else "auto"
            await self.mpv.command("sub-add", str(path), flag, f"{langs.name(lang)} (live-translate)", lang)
            if old is not None:
                await self.mpv.command("sub-remove", old)
            new = await self._find(path)
            self.ids[kind] = new
            self.debug(f"track {kind}: {'replaced ' + str(old) + ' -> ' if old is not None else 'added '}{new}")
            if old is not None and new is not None:
                if sid == old:
                    await self.mpv.set("sid", new)
                elif secondary == old:
                    await self.mpv.set("secondary-sid", new)
                elif sid not in (None, False, "no") and await self.mpv.get("sid") != sid:
                    await self.mpv.set("sid", sid)


async def serve(args, config, providers):
    mpv = Mpv(args.ipc)
    await mpv.connect()
    try:
        await _serve(mpv, args, config, providers)
    finally:
        mpv.close()


async def _serve(mpv, args, config, providers):
    try:
        transcriber, translator = providers(config, args.fake)
        ffmpeg = Ffmpeg(args.ffmpeg)
    except Exception as e:
        await mpv.osd(f"live-translate: {e}", 8)
        raise

    log_path = None
    if args.debug:
        log_path = cfg.cache_dir() / "logs" / f"{args.file.stem}-{time.strftime('%Y%m%d-%H%M%S')}.log"
    log = DebugLog(log_path)
    tracks = Tracks(mpv, log)
    panel = {"visible": bool(args.debug)}
    panel_now = asyncio.Event()

    def updated(s):
        tracks.request_update(s)
        ready = s.ready_until()
        status = f"ready to {_clock(ready)}" if ready else "working"
        asyncio.get_running_loop().create_task(mpv.osd(f"live-translate: {status} · ${s.cost:.2f}", 2))

    def message(text):
        asyncio.get_running_loop().create_task(mpv.osd(f"live-translate: {text}", 5))

    session = Session(
        args.file, config, transcriber, translator, ffmpeg, cfg.cache_dir(),
        on_update=updated, on_message=message, debug=log,
    )
    log(f"connected to mpv at {args.ipc}")
    duration = await mpv.get("duration")
    await session.prepare(duration)
    session.set_position(await mpv.get("time-pos") or 0.0)
    await mpv.osd("live-translate: started", 2)

    poll_now = asyncio.Event()

    def on_client_message(data):
        message = (data.get("args") or [None])[0]
        if message == STOP_MESSAGE:
            log("stop requested")
            session.stop()
        elif message == DEBUG_MESSAGE:
            panel["visible"] = not panel["visible"]
            panel_now.set()

    def on_end_file(data):
        log("file closed")
        session.stop()

    mpv.on_event("client-message", on_client_message)
    mpv.on_event("end-file", on_end_file)
    mpv.on_event("seek", lambda data: poll_now.set())
    mpv.on_event("playback-restart", lambda data: poll_now.set())

    async def follow():
        while True:
            try:
                await asyncio.wait_for(poll_now.wait(), POLL_S)
            except TimeoutError:
                pass
            poll_now.clear()
            path = await mpv.get("path")
            if not path or not _same_file(path, args.file):
                log("mpv moved to another file")
                session.stop()
                return
            pos = await mpv.get("time-pos")
            if pos is not None:
                session.set_position(pos)

    async def watch_disconnect():
        await mpv.closed.wait()
        log("mpv disconnected")
        session.stop()

    async def draw_panel():
        shown = False
        while True:
            try:
                if panel["visible"]:
                    await mpv.command("osd-overlay", OVERLAY_ID, "ass-events", ass_panel(panel_lines(session, log)))
                    shown = True
                elif shown:
                    await mpv.command("osd-overlay", OVERLAY_ID, "none", "")
                    shown = False
            except MPVError as e:
                log(f"debug panel: mpv refused osd-overlay: {e}")
            try:
                await asyncio.wait_for(panel_now.wait(), POLL_S)
            except TimeoutError:
                pass
            panel_now.clear()

    helpers = [asyncio.create_task(c) for c in (tracks.run(), follow(), watch_disconnect(), draw_panel())]
    try:
        await session.run()
    finally:
        for task in helpers:
            task.cancel()
        await asyncio.gather(*helpers, return_exceptions=True)
        log(f"stopped, session cost ${session.cost:.4f}")
        if not mpv.closed.is_set():
            try:
                if tracks.pending():
                    await tracks._sync(session)  # windows that landed during the stop grace period
                await mpv.command("osd-overlay", OVERLAY_ID, "none", "")
            except (MpvGone, MPVError):
                pass
            await mpv.osd(f"live-translate: stopped · ${session.cost:.2f}", 3)
        log.close()
