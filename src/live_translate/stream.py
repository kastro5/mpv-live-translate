"""Direct http(s) video links, like PikPak's "Copy link": identity, a name for the subtitles,
and telling an expired link apart from a network hiccup."""

import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from .cache import SAMPLE, content_key, tail_start

TIMEOUT_S = 20
USER_AGENT = "live-translate (+https://github.com/kastro5/mpv-live-translate)"
_URL = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_CONTENT_RANGE = re.compile(r"bytes \d+-\d+/(\d+)")
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class LinkError(Exception):
    pass


class LinkGone(LinkError):
    """The server refused the link (401/403/404/410): it expired or was revoked. No retry fixes it."""

    def __init__(self, status):
        super().__init__(f"the link stopped working ({status}). Links expire: copy a fresh one and open it again.")


def is_url(video):
    return bool(_URL.match(str(video)))


def same_video(a, b):
    if is_url(a) or is_url(b):
        return str(a) == str(b)
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def log_stem(video):
    """For debug log file names; a link's name isn't known until it is probed."""
    return "stream" if is_url(video) else PurePosixPath(str(video).replace("\\", "/")).stem


@dataclass(frozen=True)
class Remote:
    key: str
    name: str  # file name for the subtitles, e.g. "Movie.mkv"


def probe(url):
    """Fetch the first and last MB with range requests: the same cache key as the downloaded
    file, and a check that the link works and can be seeked before any money is spent."""
    head, size, headers = _get(url, 0, SAMPLE - 1)
    tail = b""
    if (start := tail_start(size)) is not None:
        tail, _, _ = _get(url, start, size - 1)
    return Remote(content_key(size, head, tail), display_name(url, headers))


def _get(url, start, end):
    request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as resp:
            if resp.headers.get_content_type() == "text/html":
                raise LinkError("that link is a web page, not a video. Use a direct link to the file (PikPak: Copy link).")
            match = _CONTENT_RANGE.match(resp.headers.get("Content-Range", ""))
            if resp.status != 206 or not match:
                raise LinkError("the server doesn't allow seeking in this link, so it can't be translated live")
            return resp.read(end - start + 1), int(match[1]), resp.headers
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404, 410):
            raise LinkGone(f"{e.code} {e.reason}") from None
        raise LinkError(f"the link returned HTTP {e.code} {e.reason}") from None
    except urllib.error.URLError as e:
        raise LinkError(f"could not reach the link: {e.reason}") from None
    except TimeoutError:
        raise LinkError("the link timed out") from None


def display_name(url, headers):
    """Content-Disposition's filename, else the URL's last path segment, else "stream"."""
    name = headers.get_filename()
    if name:
        try:
            name = name.encode("latin-1").decode("utf-8")  # http.client decodes raw UTF-8 headers as latin-1
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    else:
        last = unquote(PurePosixPath(urlsplit(url).path).name)
        name = last if "." in last else ""
    name = _UNSAFE.sub("_", name).strip(" .")[:150]
    return name or "stream"
