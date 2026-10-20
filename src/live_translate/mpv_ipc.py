"""asyncio wrapper over python-mpv-jsonipc (which handles Windows named pipes and Unix sockets)."""

import asyncio
import time

from python_mpv_jsonipc import MPV, MPVError

PIPE_PREFIX = "\\\\.\\pipe\\"


class MpvGone(Exception):
    pass


class Mpv:
    def __init__(self, ipc):
        # python-mpv-jsonipc wants the bare pipe name on Windows
        self.ipc = ipc[len(PIPE_PREFIX):] if ipc.startswith(PIPE_PREFIX) else ipc
        self.closed = asyncio.Event()
        self._mpv = None

    async def connect(self, timeout=5.0):
        """mpv starts its IPC server in the background, so the socket may not exist yet."""
        loop = asyncio.get_running_loop()
        deadline = time.monotonic() + timeout

        def lost():
            loop.call_soon_threadsafe(self.closed.set)

        while True:
            try:
                self._mpv = await asyncio.to_thread(MPV, start_mpv=False, ipc_socket=self.ipc, quit_callback=lost)
                return
            except (OSError, MPVError):
                if time.monotonic() > deadline:
                    raise MpvGone(f"Could not connect to mpv at {self.ipc}") from None
                await asyncio.sleep(0.2)

    async def command(self, *args):
        if self.closed.is_set():
            raise MpvGone("mpv closed")
        try:
            return await asyncio.to_thread(self._mpv.command, *args)
        except (BrokenPipeError, ConnectionError) as e:
            raise MpvGone("mpv closed") from e

    async def get(self, name, default=None):
        try:
            return await self.command("get_property", name)
        except MPVError:
            return default  # e.g. property unavailable

    async def set(self, name, value):
        await self.command("set_property", name, value)

    async def osd(self, text, seconds=3.0):
        try:
            await self.command("show-text", text, int(seconds * 1000))
        except (MpvGone, MPVError):
            pass

    def on_event(self, name, callback):
        """callback(data) runs on the asyncio loop, not the IPC thread."""
        loop = asyncio.get_running_loop()
        self._mpv.bind_event(name, lambda data: loop.call_soon_threadsafe(callback, data))

    def close(self):
        if self._mpv:
            self._mpv.terminate(join=False)
