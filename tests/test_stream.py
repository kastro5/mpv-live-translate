import asyncio
import dataclasses
import hashlib
import http.client
import http.server
import os
import re
import shutil
import subprocess
import threading

import pytest

from live_translate.audio import Ffmpeg
from live_translate.cache import file_key
from live_translate.config import Config
from live_translate.session import FatalError, Session
from live_translate.stream import LinkError, LinkGone, display_name, is_url, probe, same_video
from live_translate.transcribe import FakeTranscriber
from live_translate.translate import FakeTranslator

from test_session import FakeFfmpeg

LINK = "/download/?fid=abc&sign=SECRET"


class RangeHandler(http.server.BaseHTTPRequestHandler):
    """Serves server.data like a CDN: byte ranges, optional Content-Disposition, or a forced error."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        srv = self.server
        if srv.status:
            self.send_error(srv.status)
            return
        data = srv.data
        m = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if m and srv.ranges:
            start = int(m[1])
            end = min(int(m[2]) if m[2] else len(data) - 1, len(data) - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.send_header("Accept-Ranges", "bytes")
        else:
            start, end = 0, len(data) - 1
            self.send_response(200)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Content-Type", srv.content_type)
        if srv.filename:
            raw = f'attachment; filename="{srv.filename}"'.encode("utf-8").decode("latin-1")
            self.send_header("Content-Disposition", raw)
        self.end_headers()
        try:
            self.wfile.write(data[start : end + 1])
        except (BrokenPipeError, ConnectionResetError):
            pass  # ffmpeg hangs up once it has what it needs


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    srv.daemon_threads = True
    srv.data, srv.status, srv.ranges = b"", None, True
    srv.content_type, srv.filename = "video/x-matroska", None
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}{LINK}"
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def old_file_key(path):
    """file_key before links existed; existing caches must keep their keys."""
    size = os.path.getsize(path)
    h = hashlib.sha256(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(1 << 20))
        if size > 1 << 20:
            f.seek(max(1 << 20, size - (1 << 20)))
            h.update(f.read(1 << 20))
    return h.hexdigest()[:20]


@pytest.mark.parametrize("size", [1000, (1 << 20) + 5000, 3 << 20])
def test_link_key_matches_downloaded_file(server, tmp_path, size):
    server.data = os.urandom(size)
    video = tmp_path / "movie.mkv"
    video.write_bytes(server.data)
    assert probe(server.url).key == file_key(video) == old_file_key(video)


def test_link_name(server):
    server.data = b"x" * 10
    assert probe(server.url).name == "stream"  # PikPak's path is just /download/
    server.filename = "[Sub] 映画: Part 1.mkv"
    assert probe(server.url).name == "[Sub] 映画_ Part 1.mkv"


def test_name_from_url_path():
    headers = http.client.HTTPMessage()
    assert display_name("https://cdn.example/films/My%20Movie.mp4?token=1", headers) == "My Movie.mp4"


def test_refused_or_unseekable_links(server):
    server.data = b"x" * 10
    server.status = 403
    with pytest.raises(LinkGone, match="403"):
        probe(server.url)
    server.status, server.ranges = None, False
    with pytest.raises(LinkError, match="seeking"):
        probe(server.url)
    server.ranges, server.content_type = True, "text/html"
    with pytest.raises(LinkError, match="web page"):
        probe(server.url)


def test_is_url_and_same_video(tmp_path):
    assert is_url("https://x/y") and is_url("HTTP://x") and not is_url(r"C:\videos\a.mkv")
    assert same_video("https://x/a?t=1", "https://x/a?t=1") and not same_video("https://x/a?t=1", "https://x/a?t=2")
    assert same_video(tmp_path / "a.mkv", str(tmp_path / "." / "a.mkv"))


def test_session_on_link_writes_to_cache_dir(server, tmp_path):
    server.data = b"x" * 5000
    server.filename = "Movie.mkv"
    conf = dataclasses.replace(Config(), mode="full")
    session = Session(server.url, conf, FakeTranscriber(), FakeTranslator(), FakeFfmpeg(130.0), tmp_path / "cache")

    async def go():
        await session.prepare()
        await session.run(until_complete=True)

    asyncio.run(go())
    assert session.outputs["translation"] == session.cache.dir / "Movie.en.srt"
    assert "[English]" in session.outputs["translation"].read_text()


def test_link_expiring_mid_session_stops_it(server, tmp_path):
    server.data = b"x" * 5000

    class ExpiringFfmpeg(FakeFfmpeg):
        async def silences(self, path, start, length):
            raise LinkGone("403 Forbidden")

    session = Session(server.url, Config(), FakeTranscriber(), FakeTranslator(), ExpiringFfmpeg(200.0), tmp_path / "c")

    async def go():
        await session.prepare()
        await session.run()

    with pytest.raises(FatalError, match="copy a fresh one"):
        asyncio.run(go())


needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")


@needs_ffmpeg
def test_ffmpeg_reads_and_seeks_a_link(server, tmp_path):
    clip = tmp_path / "clip.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=10", "-f", "lavfi",
         "-i", "anullsrc=r=44100:cl=mono:d=3", "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
         "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", "-c:a", "flac", str(clip)],
        check=True,
    )
    server.data = clip.read_bytes()
    ffmpeg = Ffmpeg()

    async def go():
        duration = await ffmpeg.duration(server.url)
        silences = await ffmpeg.silences(server.url, 5.0, 12.0)
        flac = await ffmpeg.flac(server.url, 15.0, 3.0)
        return duration, silences, flac

    duration, silences, flac = asyncio.run(go())
    assert duration == pytest.approx(23.0, abs=0.1)
    assert any(s < 10.5 and e > 12.5 for s, e in silences)
    assert flac.startswith(b"fLaC")


@needs_ffmpeg
def test_ffmpeg_reports_expired_link_without_leaking_it(server):
    server.status = 403
    with pytest.raises(LinkGone) as e:
        asyncio.run(Ffmpeg().flac(server.url, 0.0, 1.0))
    assert "403" in str(e.value) and "SECRET" not in str(e.value)
