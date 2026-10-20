"""What the pipeline is doing, for the mpv debug panel, the terminal and a log file."""

import time
from collections import deque


def clock(seconds):
    """Position in the video: 12:31.4 or 1:02:31.4"""
    m, s = divmod(max(0.0, seconds), 60)
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02}:{s:04.1f}" if h else f"{m}:{s:04.1f}"


class DebugLog:
    """Keeps the last few events in memory. With a path, also appends every event to a file;
    with echo, also prints it."""

    def __init__(self, path=None, keep=10, echo=None):
        self.events = deque(maxlen=keep)
        self.path = path
        self.echo = echo
        self._file = None
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = path.open("a", encoding="utf-8")

    def __call__(self, text, window=None):
        line = f"{time.strftime('%H:%M:%S')}  " + (f"#{window + 1} " if window is not None else "") + text
        self.events.append(line)
        if self._file:
            self._file.write(line + "\n")
            self._file.flush()
        if self.echo:
            self.echo(line)

    def close(self):
        if self._file:
            self._file.close()
            self._file = None


def no_debug(text, window=None):
    pass


def ranges(indices):
    """{0, 1, 2, 5} -> '1-3, 6' (windows numbered from 1 like the events)"""
    out, run = [], []
    for i in sorted(indices):
        if run and i == run[-1] + 1:
            run.append(i)
        else:
            if run:
                out.append(run)
            run = [i]
    if run:
        out.append(run)
    return ", ".join(f"{r[0] + 1}-{r[-1] + 1}" if len(r) > 1 else f"{r[0] + 1}" for r in out) or "-"


def panel_lines(session, log):
    ready = session.ready_until()
    running = ", ".join(f"{i + 1} {stage}" for i, stage in sorted(session.stages.items())) or "-"
    lines = [
        f"live-translate DEBUG   pos {clock(session.position)}   ready to {clock(ready) if ready else '-'}"
        f"   ${session.cost:.3f}",
        f"windows {len(session.starts)}   done {ranges(session.results)}   failed {ranges(session.failed)}",
        f"running {running}",
        *log.events,
    ]
    if log.path:
        lines.append(f"log: {log.path}")
    return lines


def ass_panel(lines, font_size=14):
    """mpv osd-overlay ASS text: small monospace block in the top-left corner."""
    def escape(text):
        return text.replace("\\", "/").replace("{", "(").replace("}", ")")

    style = f"{{\\an7\\fs{font_size}\\bord1.5\\fnmonospace}}"
    return style + "\\N".join(escape(line) for line in lines)
