"""ffmpeg helpers: duration, silence near a seam, and cutting a span to FLAC for Scribe."""

import asyncio
import re
import shutil
from pathlib import Path

from .stream import LinkGone, is_url
from .windows import parse_silences

SILENCE_NOISE = "-35dB"
# Ride out dropped connections on links; 4xx (an expired link) still fails at once
RECONNECT = ("-reconnect", "1", "-reconnect_on_network_error", "1", "-reconnect_delay_max", "5")
_REFUSED = re.compile(r"Server returned (4\d\d [^(\r\n]*|4XX)")


class AudioError(Exception):
    pass


class Ffmpeg:
    def __init__(self, ffmpeg=None):
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg")
        if not self.ffmpeg:
            raise AudioError("ffmpeg not found. Install it or set its path with --ffmpeg.")
        # ffprobe ships next to ffmpeg
        exe = Path(self.ffmpeg)
        probe = exe.with_name(exe.name.replace("ffmpeg", "ffprobe"))
        self.ffprobe = str(probe) if probe.exists() else shutil.which("ffprobe")

    async def _run(self, *args):
        proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            out, err = await proc.communicate()
        except asyncio.CancelledError:
            proc.kill()
            raise
        err = err.decode(errors="replace")
        if proc.returncode != 0:
            if refused := _REFUSED.search(err):
                raise LinkGone(refused[1].strip())
            for arg in args:
                if is_url(arg):
                    err = err.replace(arg, "<link>")  # signed links are as good as a password until they expire
            raise AudioError(f"{Path(args[0]).name} failed: {err.strip()[-500:]}")
        return out, err

    @staticmethod
    def _input(path):
        return [*RECONNECT, "-i", str(path)] if is_url(path) else ["-i", str(path)]

    async def duration(self, path):
        if not self.ffprobe:
            raise AudioError("ffprobe not found next to ffmpeg or on PATH.")
        out, _ = await self._run(
            self.ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", *self._input(path)
        )
        try:
            return float(out.decode().strip())
        except ValueError:
            raise AudioError(f"Could not read the duration of {'the link' if is_url(path) else path}") from None

    async def silences(self, path, start, length):
        """Absolute (start, end) silences in [start, start + length)."""
        _, err = await self._run(
            self.ffmpeg, "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
            *self._input(path), "-vn", "-af", f"silencedetect=n={SILENCE_NOISE}:d=0.3", "-f", "null", "-",
        )
        return parse_silences(err, start, length)

    async def flac(self, path, start, length):
        """Mono 16 kHz FLAC bytes of [start, start + length)."""
        out, _ = await self._run(
            self.ffmpeg, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", *self._input(path),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "flac", "-f", "flac", "pipe:1",
        )
        return out
