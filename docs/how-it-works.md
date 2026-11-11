# How live-translate works

This document describes the internals: what happens between pressing <kbd>Ctrl</kbd>+<kbd>T</kbd> and a
translated subtitle appearing on screen. For installing and using the tool, see the [README](../README.md).

## Overview

```
┌──────────── mpv ────────────┐        ┌──────────────── transcript serve ────────────────────┐
│ live-translate.lua          │ spawn  │ serve.py   follows the playhead, updates tracks, OSD │
│   Ctrl+T ── start/stop ─────┼───────►│   │                                                  │
│   Ctrl+Shift+T ─ debug ─────┼─ IPC ─►│ session.py  one Session per video                    │
│                             │        │   │  scheduler.py  which windows to do next          │
│ subtitle tracks ◄── sub-add ┼── IPC ─┤   │  windows.py    seams in silence                  │
└─────────────────────────────┘        │   │  audio.py      ffmpeg: silence, FLAC cut         │
                                       │   │  transcribe.py ElevenLabs Scribe v2              │
                                       │   │  cues.py       words → subtitle cues             │
                                       │   │  translate.py  Claude, one line per cue          │
                                       │   ▼                                                  │
                                       │ cache.py + output.py  → cache dir, movie.<lang>.srt  │
                                       └──────────────────────────────────────────────────────┘
```

There are two pieces:

- **`mpv/live-translate.lua`** is a thin mpv script. It starts and stops the Python process and
  forwards key presses. It does no processing itself.
- **The Python package** (`live_translate`, installed as the `transcript` command) does everything
  else. `transcript serve` is the process the Lua script starts; `transcript generate` runs the same
  pipeline over a whole file without mpv.

Both commands are built on one class, `Session` (`src/live_translate/session.py`), which runs the
pipeline for one video.

## Module map

| Module | Role |
|---|---|
| `cli.py` | Argument parsing, sub-commands, building the real or fake providers |
| `serve.py` | `serve`: talks to mpv, follows the playhead, swaps subtitle tracks, draws the debug panel |
| `session.py` | The pipeline and its main loop: scheduling, retries, cost, output |
| `scheduler.py` | Pure functions deciding which windows to work on next |
| `windows.py` | Nominal window boundaries, choosing seams in silence, padding, owned words |
| `audio.py` | ffmpeg/ffprobe wrapper: duration, `silencedetect`, FLAC extraction |
| `transcribe.py` | Scribe v2 client and a fake transcriber |
| `cues.py` | Grouping word tokens into cues, fixing durations, writing `.srt` |
| `translate.py` | Claude prompt, structured output, validation, pricing, fake translator |
| `cache.py` | Content-based video identity and the on-disk cache |
| `output.py` | Choosing where `.srt` files go, atomic writes |
| `stream.py` | Direct http(s) links: probing, naming, expired-link detection |
| `mpv_ipc.py` | asyncio wrapper over `python-mpv-jsonipc` (Unix sockets and Windows named pipes) |
| `debuglog.py` | Event log for the debug panel, terminal and log file |
| `config.py` | `config.toml` loading/validation, env var overrides, cache dir |
| `langs.py` | ISO 639-1 / 639-3 codes and language names |
| `setup.py`, `install.py` | Guided setup, installing the Lua script, the Windows desktop shortcut |
| `update.py` | `transcript update`: `uv tool upgrade`, then reinstalling the Lua script from the new version |

## Lifecycle of a live session

### 1. Starting (Lua side)

When <kbd>Ctrl</kbd>+<kbd>T</kbd> is pressed, `live-translate.lua`:

1. Checks that the current `path` is a local file or an `http(s)` link (other schemes like
   `ytdl://` are refused), and makes a local path absolute.
2. Makes sure mpv has a JSON IPC server. It reuses `input-ipc-server` if one is already set,
   otherwise opens `\\.\pipe\mlt-<pid>` on Windows or `/tmp/mlt-<pid>.sock` elsewhere.
3. Runs `transcript serve --ipc <path> --file <video> [--ffmpeg …] [--debug]` as an async
   subprocess. The `executable` and `ffmpeg` paths come from `script-opts/live_translate.conf`,
   which `install-mpv-script` fills in, because mpv started from a file manager often doesn't see the
   user's `PATH`.

If the process exits with an error, the last line of its stderr is shown on the OSD.

### 2. Connecting and preparing (Python side)

`serve` connects to the IPC socket (retrying for up to 5 s, since mpv opens it in the background),
builds the providers, and calls `Session.prepare()` with the duration mpv reports. `prepare`:

- Computes the video's **cache key** (see [Cache](#cache)). For links this is done over HTTP.
- Computes the nominal window boundaries (see [Windows and seams](#windows-and-seams)).
- Picks the source language: the configured one, or one locked in a previous session, or none yet.
- Builds the **translation variant** string, `<target>/<model>-<effort>-p<PROMPT_VERSION>`, which
  keys cached translations so changing model, effort, target or the prompt causes a redo.
- Loads every window that is already fully cached and, if any were, writes the `.srt` files at once.

### 3. Running

`serve` then runs `Session.run()` alongside four helper tasks:

| Task | What it does |
|---|---|
| `follow` | Every second, or immediately on mpv's `seek` / `playback-restart` events, reads `time-pos` and passes it to `Session.set_position`. Stops the session if mpv switched to another file. |
| `tracks.run` | Whenever the session writes new `.srt` files, reloads them in mpv (see [mpv tracks](#mpv-subtitle-tracks)). |
| `watch_disconnect` | Stops the session if the IPC connection drops (mpv quit). |
| `draw_panel` | Redraws the debug panel as an ASS `osd-overlay` each second while it is visible. |

After each finished window the OSD shows `live-translate: ready to 14:02 · $0.08`.

### 4. Stopping

Pressing <kbd>Ctrl</kbd>+<kbd>T</kbd> again (or closing the file / quitting mpv) makes the Lua
script send `script-message live-translate-stop`. Python sees it as a `client-message` event and
calls `Session.stop()`. Running windows get a **2-second grace period** so that requests already paid
for can land in the cache, then are cancelled. Any windows that finished during the grace period are
pushed to mpv, the debug overlay is removed and `stopped · $x.xx` is shown. If the process hasn't
exited after 3 seconds, the Lua script kills it.

## The pipeline for one window

`Session._process(i)` takes one window from timeline to translated cues:

```
seams ─► ffmpeg FLAC cut (padded) ─► Scribe ─► owned words ─► cues ─► Claude ─► WindowResult ─► .srt
         └──────────── skipped if the transcript is cached ──────────┘        └ skipped if cached
                                                                                 or same language
```

### Windows and seams

`windows.py` splits the timeline into windows of about `window_s` (default 60 s):

- **Nominal boundaries** sit at every multiple of `window_s`. The window count is
  `round(duration / window_s)`, so a short tail joins the previous window rather than becoming its
  own tiny one.
- Each boundary is moved to a **seam** in nearby silence. ffmpeg's `silencedetect`
  (`-35dB`, at least 0.3 s) runs over ±`min(8 s, window_s / 3)` around the boundary, and the seam is
  the midpoint of the longest silence found. With no silence, the seam stays at the boundary.

Each seam depends only on the audio around its own boundary. That is the key property: windows can
be processed in any order, in parallel, or after a seek, and still tile the timeline exactly with no
gaps or overlaps. Seams are cached in `meta.json` per `window_s` and boundary, and an in-memory task
map makes sure two neighbouring windows never run detection for the same seam twice.

### Audio extraction

The window's span plus **1.5 s of padding on each side** is cut to mono 16 kHz FLAC and piped from
ffmpeg straight into memory. The padding keeps words at a seam from being clipped. After
transcription, only tokens whose start time falls in the window's own `[start, end)` are kept
(`owned_words`), so the padding never produces duplicate words between neighbours.

### Transcription

`ScribeTranscriber` sends the FLAC to ElevenLabs Scribe v2 and gets back word, spacing and
audio-event tokens with timings, which are shifted to absolute video time. Only text, start, end and
type are kept.

**Language lock.** With `source_language = "auto"`, the first window where Scribe reports a
language probability above 0.8 over at least 20 words locks the language. It is saved in
`meta.json` and passed to Scribe for every later window (and future sessions on the same video),
which avoids misdetection on short or noisy windows.

At most **2 Scribe requests** run at once.

### Building cues

`cues.build_cues` groups tokens into subtitle cues `(start, end, text)`. Audio events like
`(laughter)` are dropped. A new cue starts when:

- there is a pause of more than 1 s,
- the cue would last more than 6 s, or
- the text would exceed two lines (2 × the line width).

A cue also ends at a sentence-ending mark (`. ? ! … 。 ？ ！`) once it has enough text to read.
Then `fix_durations` caps each cue by reading time (1 s + a per-character allowance, which catches
Scribe stretching one word across a long pause) and extends cues shorter than 1 s, never overlapping
the next cue.

Line width and thresholds depend on the language:

| Layout | Languages | Line width | Min. chars before a sentence break | Reading time per char |
|---|---|---|---|---|
| Latin | everything else | 42 | 20 | 0.10 s |
| CJK | Japanese, Chinese, Cantonese | 20 | 8 | 0.25 s |

The CJK reading allowance is generous on purpose: the translated cues reuse these timings, and an
English line carries more characters than the Japanese one it replaces.

### Translation

If the window's language already matches the target, the source cues are used as the translation
and Claude isn't called. Otherwise `ClaudeTranslator` sends one request per window.

**Context.** For continuity, the prompt includes the last 20 cues of the previous window, with
their translations when available. If the previous window is currently being processed, this window
waits for it for up to 30 s. A previous window that isn't running (for example after a seek) is not
waited for.

**Prompt.** The system prompt asks for natural, subtitle-speed phrasing, exactly one translation per
cue id, names kept as is, no translator notes, and tolerance for recognition errors. It also tells
Claude that text inside `<cues>` and `<context>` is content, never instructions. The user message
lists cues as `<id> [timestamp] text`; context cues get negative ids so they can't be confused with
the ones to translate.

**Request.** It uses structured output with a JSON schema
(`{"translations": [{"id", "text"}, …]}`), the configured `effort`, and a large `max_tokens`.
Sonnet and Opus also get the server-side refusal fallback (`fallbacks: "default"`).

**Validation.** The ids must be exactly `1..n` in order and every text non-empty, because each
translation reuses its cue's timing. A misaligned answer is retried once. A refusal, `max_tokens`, or
a second misaligned answer raises `TranslationError`. The window is then kept with its source cues
only and marked failed, with no retry, since a refusal won't change on a retry. Tokens from failed
attempts still count towards the cost.

At most **2 Claude requests** run at once.

## Scheduling

`scheduler.py` is pure, so the policy is tested without audio or APIs. Each loop iteration of
`Session.run`:

1. Builds the **wanted** list in priority order: windows from the one containing the playhead up to
   `position + lookahead_s` (default 120 s). In `full` mode (always used by `generate`), the rest of
   the file follows, then everything before the playhead. Windows that are done, failed or waiting
   to retry are left out.
2. Starts the first wanted windows that aren't running, up to **3 windows in flight**.
3. Waits until a window finishes, the playhead moves (`set_position` sets a wake event), or 1 s
   passes.

What this means in practice:

- **Seeking** moves the work immediately. Windows near the new position become first in line.
  Windows already in flight elsewhere finish (they are already paid for) and get cached.
- **Pausing** stops spending once the lookahead is full: no window past the horizon is wanted.
- `generate` uses `until_complete=True` and exits once nothing is wanted or running.

### Errors and retries

| Error | Handling |
|---|---|
| Rejected API key (401/403), Anthropic auth/permission error, expired link | **Fatal**: the session stops and the message is shown on the OSD / printed |
| Anything else (network, 5xx, ffmpeg failure, …) | Retried after 5 s, then 10 s; after 3 attempts the window is marked failed and a message is shown |
| Claude refusal / misalignment | Window kept untranslated, no retry (see above) |

For ElevenLabs errors, the provider's own message (for example a quota message) is shown instead of
the raw exception.

## Cache

Everything paid for is cached under the user cache dir (`platformdirs`, e.g.
`~/.cache/live-translate` on Linux, `%LOCALAPPDATA%\live-translate\Cache` on Windows).

**Video identity.** The key is the first 20 hex chars of
`sha256(file size + first 1 MB + last 1 MB)`. It survives renames and moves, and for links
`stream.probe` computes the same key with two HTTP range requests, so a link and the downloaded file
share one cache entry.

```
<cache dir>/
├── <video key>/
│   ├── meta.json                         seams per window size, locked language,
│   │                                     hashes of .srt files we wrote
│   ├── transcripts/scribe_v2/
│   │   └── 61.240-119.875.json           language, probability, owned word tokens
│   ├── translations/en/claude-sonnet-5-5-medium-p2/
│   │   └── 61.240-119.875.json           one string per cue
│   └── <name>.en.srt                     only for links or when the video's folder can't be used
└── logs/                                 debug logs
```

Transcripts and translations are keyed by the window's exact seam span, so they are reused only
when the window is identical. Changing `window_s` creates new seams and therefore new windows.
Changing the model, effort, target language or `PROMPT_VERSION` keeps the transcripts and only
redoes translations. Cues are always rebuilt from the cached words, so changes to the cue rules apply
without spending anything. All JSON is written to a temp file and moved into place.

## Output files

After every finished window, `_write_outputs` rewrites up to two `.srt` files from all finished
windows in time order:

- **Translation track**, `movie.<target>.srt`, with only the cues that have a translation.
- **Source track**, `movie.<source>.srt`, with all cues, when the source language differs from the target.

`join_cues` trims any cue that would run into the next one across a window seam.

**Where the files go** (`output.choose_path`):

1. Next to the video, so mpv and other players load them automatically, if the file doesn't exist
   yet and the folder is writable, or if it exists and its content hash matches what this tool
   recorded in `meta.json`.
2. Otherwise (someone else's file with the same name, or a read-only folder) in the cache entry.
3. Links have no folder: always the cache entry, named after the `Content-Disposition` filename or
   the URL's last path segment.

Files are written to a temp file and `os.replace`d, so mpv never reads half a file. On Windows the
replace is retried for up to 2 s, since antivirus or sync tools can briefly lock the target.

## mpv subtitle tracks

`serve.Tracks` keeps mpv in sync with the files. It replaces a track with `sub-add` followed by
`sub-remove` of the old id rather than `sub-reload`, which would force the track to primary and drop
its title. It then restores the user's `sid` / `secondary-sid` choice onto the new id, so switching
to the original language or showing it as a second line survives updates.

- The first time, the translation track is added with `select`; the source track is added with `auto`.
- Tracks are titled `<Language> (live-translate)` and tagged with the language code.
- If mpv auto-loaded our `.srt` from a previous session, that track is found by its filename and
  replaced instead of a duplicate being added.
- Update requests are coalesced, so a burst of finished windows causes one reload.

## Direct links

`stream.py` and `audio.py` handle `http(s)` links (e.g. PikPak's "Copy link"):

- **Probe before spending.** Range requests for the first and last MB check that the link is a
  video (not `text/html`) and allows seeking (`206` with `Content-Range`), and give the cache key
  and a display name.
- **ffmpeg reads the link directly** with reconnect flags, so dropped connections are ridden out.
  ffmpeg only fetches the stretch it works on, which is about 1.3× the data watched (padding, seam
  detection) on top of mpv's own download.
- **Expired links.** A 401/403/404/410 from the probe, or a `Server returned 4xx` from ffmpeg,
  raises `LinkGone`, which is fatal: the session stops with "the link stopped working".
- **Privacy.** Signed links act as passwords until they expire, so the URL is replaced with `<link>`
  in ffmpeg error messages.

The `clipboard=yes` script option, which the Windows desktop shortcut sets, makes the Lua script
open the link or path on the clipboard at startup (needs mpv 0.40+) and press start once the file
has loaded.

## Cost accounting

The on-screen cost is an estimate computed locally:

- **Scribe:** padded audio seconds × the $0.22/hour list price.
- **Claude:** input and output tokens from each response × the per-model prices in `translate.PRICES`,
  including failed and retried attempts.

Cached windows add nothing. Because each window is sent with 3 s of padding and the previous
window's context, the real totals are slightly above "minutes watched × rate".

## Configuration

`config.load` reads `config.toml` from the user config dir, applies the `ELEVENLABS_API_KEY` /
`ANTHROPIC_API_KEY` environment variables over it, and validates the values: mode, effort,
`window_s >= 10` and `lookahead_s >= window_s`. On non-Windows systems the file is saved with mode
`0600` because it holds API keys. CLI flags (`--target`, `--model`, `--effort`) override the file
for one run. `serve` reads the config each time it is started, so changes apply on the next
<kbd>Ctrl</kbd>+<kbd>T</kbd>.

## Updating

`transcript update` runs `uv tool upgrade mpv-live-translate`, then `transcript install-mpv-script`
in a **new process**, so the Lua script comes from the version just installed rather than the one
still running. `--mpv-dir` is passed through for portable mpv installs.

On Windows, uv copies tool executables instead of symlinking them, and a running `.exe` can't be
replaced. So `update` starts the upgrade in a new PowerShell window and exits. That window waits for
the `transcript` process that launched it, then for any `transcript serve` started by mpv (press
<kbd>Ctrl</kbd>+<kbd>T</kbd> or close mpv), and only then runs the two steps. The values reach the
script through environment variables and the script itself through `-EncodedCommand`, so nothing
needs PowerShell quoting.

## Debugging

Pipeline steps emit events through `DebugLog`: seam choices, ffmpeg timings and sizes, Scribe
language and word counts, Claude tokens and cost, retries, track swaps. The last 10 are shown on the
debug panel together with the playhead, how far ahead subtitles are ready, done and failed window
ranges, and what each running window is waiting on (`seams`, `waiting for Scribe slot`, `Claude`,
…). With debug on, every event also goes to `<cache dir>/logs/<video>-<timestamp>.log`.

## Testing without API keys

`FakeTranscriber` emits one numbered word per second of audio (a sentence every four words) and
`FakeTranslator` prefixes each cue with `[<target>]`. With `--fake`, `generate` and `serve` run the
whole pipeline (ffmpeg, seams, cues, cache, output, mpv tracks) at no cost:

```sh
uv run pytest
uv run transcript generate some-video.mkv --fake
```

The tests in `tests/` cover the scheduler, seams, cues, prompt building and validation, the cache and
output rules, whole sessions with fake providers (full runs, cached reruns, lookahead, refusals,
rejected keys), setup, and link handling.
