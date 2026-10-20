# mpv-live-translate

Translated subtitles for any video in [mpv](https://mpv.io), generated **while you watch**. Press
<kbd>Ctrl</kbd>+<kbd>T</kbd> and subtitles appear a few seconds later, kept a couple of minutes ahead
of the playhead. Speech recognition is [ElevenLabs Scribe v2](https://elevenlabs.io/speech-to-text)
and translation is [Claude](https://www.anthropic.com/claude). In the author's testing, Scribe was much
more accurate than local Whisper on hard audio like streams and casual Japanese speech.

- Only the part you watch is processed. Pausing stops spending once the buffer is full.
- Seeking jumps the work to the new spot.
- Results are cached, and `movie.en.srt` / `movie.ja.srt` are written next to the video, so a
  rewatch is free and any player can use the files later.
- The original-language track is loaded too, so you can switch to it or show it as a second line.
- Works on Windows, macOS and Linux.

## How it works

```
mpv ──Ctrl+T──► live-translate.lua ──starts──► live-translate serve
                       ▲                              │ follows the playhead over mpv's IPC
                       └──── subtitle tracks ◄────────┤ 60 s windows, cut in silence:
                                                      │ ffmpeg → Scribe → subtitle cues → Claude
                                                      └► movie.en.srt + movie.ja.srt + cache
```

The video is split into ~60-second windows whose boundaries are moved into nearby silence, so no
sentence is cut in half. Each window is transcribed and translated with the previous window as
context, and the subtitle files are rewritten as windows finish.

## Cost

You pay ElevenLabs and Anthropic directly with your own API keys. Rough figures with the defaults
(Claude Sonnet 5.5, medium effort) on Japanese speech; the translation part was measured, the
transcription part is ElevenLabs' list price:

| | per hour watched |
|---|---|
| Scribe v2 transcription | ~$0.23 |
| Claude Sonnet 5.5 translation | ~$0.50 |
| **Total** | **~$0.75** |

The running cost for the session is shown on screen. Rewatching cached parts costs nothing.

## Install

You need [mpv](https://mpv.io/installation/), [ffmpeg](https://ffmpeg.org/download.html),
[uv](https://docs.astral.sh/uv/getting-started/installation/), and API keys for
[ElevenLabs](https://elevenlabs.io/app/settings/api-keys) and
[Anthropic](https://platform.claude.com/settings/keys) (an ElevenLabs key limited to speech-to-text
is enough).

```sh
uv tool install git+https://github.com/kastro5/mpv-live-translate
live-translate setup
```

`setup` asks for your keys (and checks them), the language to translate into and the model, then
installs the mpv script. Run it again any time to change those.

### Windows quick start

In PowerShell:

```powershell
winget install Git.Git
winget install Gyan.FFmpeg
winget install astral-sh.uv
winget install shinchiro.mpv
# close and reopen PowerShell so the new commands are on PATH
uv tool install git+https://github.com/kastro5/mpv-live-translate
live-translate setup
```

- Use plain mpv (the `shinchiro.mpv` build above). mpv.net is a different player with its own
  config folder and key bindings, and isn't tested.
- If your mpv folder has a `portable_config` folder next to `mpv.exe`, mpv reads its settings from
  there: answer *n* when `setup` offers to install the script and run
  `live-translate install-mpv-script --mpv-dir "C:\path\to\mpv\portable_config"` instead.
- To open videos in mpv by double-clicking, right-click a video → *Open with* → *Choose another
  app* → mpv.

### Updating

```sh
uv tool upgrade mpv-live-translate
live-translate install-mpv-script
```

## Use

Open a video in mpv and press <kbd>Ctrl</kbd>+<kbd>T</kbd>. Press it again to stop.

- The translation is selected automatically. Cycle subtitle tracks with <kbd>j</kbd> as usual.
- To show the original as a second line, add `ctrl+j cycle secondary-sid` to mpv's `input.conf`.
- To change the key, set `key=...` in `script-opts/live_translate.conf`, or bind
  `script-binding live_translate/toggle` in `input.conf`.

### Debug panel

Press <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>T</kbd> while it runs to show or hide a panel with
what each step is doing: playhead, how far ahead subtitles are ready, cost so far, which windows
are done or running (and on what), and the latest events (silence cuts, ffmpeg, Scribe and Claude
timings, tokens, retries and errors, track updates).

```
live-translate DEBUG   pos 12:31.0   ready to 14:02.3   $0.083
windows 120   done 10-12   failed -
running 13 Claude, 14 Scribe
22:31:06  #13 Scribe done 3.1s  jpn 0.98  212 words  $0.0039
22:31:06  #13 Claude start  28 cues, context 20
22:31:10  #12 Claude done 4.4s  2146 in / 546 out  $0.0098
```

With `debug = true` in the settings (or answering *y* in `setup`), the panel is shown from the
start and every event is also written to a log file (its path is on the panel's last line) under the
cache folder's `logs/`. `live-translate generate --debug` prints the same events in the terminal. If something goes
wrong, that log file is the thing to attach to an issue.

### Ahead of time

Whole files can also be done ahead of time without mpv:

```sh
live-translate generate movie.mkv
```

That fills the same cache, so playing the file afterwards is instant and free.

### Links (PikPak and other direct links)

Direct `http(s)` links to a video file work too, for example a cloud drive's download link:

1. In PikPak, use **Copy link** on the video.
2. Open mpv with no file (double-click `mpv.exe` on Windows) and press <kbd>Ctrl</kbd>+<kbd>V</kbd>
   to play the copied link.
3. Press <kbd>Ctrl</kbd>+<kbd>T</kbd> as usual.

On Windows, `live-translate shortcut` (also offered by `setup`) puts a **Translate copied link**
shortcut on the desktop that does steps 2 and 3 in one go: copy a link, open the shortcut. It needs
mpv 0.40 or newer. It also works with a copied file path.

Things that differ from local files:

- The subtitle files go in the cache folder, named after the video (the debug log
  shows the path). Opening a fresh link to the same video later reuses everything already done, and so does
  downloading the video, because both are identified by their content.
- ffmpeg downloads the stretch it is working on separately from mpv: about 1.3× the data of the part
  you watch, on top of mpv's own download. Fine on a fast connection. On a slow one it may compete with
  playback.
- Links like PikPak's expire after a day or two. If one expires mid-way, live-translate stops and
  says so. Copy a fresh link and open it again.
- The link has to point at the video file itself and allow seeking. Web pages (YouTube and the
  like) don't work.

`live-translate generate "<link>"` works on links as well.

## Settings

`live-translate setup` covers the common ones. For the rest, `live-translate config --edit` opens
`config.toml` in a text editor (Notepad on Windows):

```toml
[transcribe]
source_language = "auto"   # or e.g. "ja" to skip detection

[translate]
target = "en"              # any language Claude writes
model = "claude-sonnet-5-5"   # claude-sonnet-5-5 | claude-opus-5-5 | claude-haiku-5-5
effort = "medium"          # low | medium | high | xhigh | max

[live]
mode = "lookahead"         # lookahead: stay ahead of the playhead; full: then do the rest of the file
lookahead_s = 120
window_s = 60
debug = false              # true: show the debug panel from the start and write log files
```

The file lives in `%LOCALAPPDATA%\live-translate\config.toml` on Windows,
`~/.config/live-translate/config.toml` on Linux and
`~/Library/Application Support/live-translate/config.toml` on macOS. The API keys can instead come
from the `ELEVENLABS_API_KEY` and `ANTHROPIC_API_KEY` environment variables, which win over the
file. Changes apply the next time you press Ctrl+T.

The mpv side has its own small file, `script-opts/live_translate.conf` in mpv's config folder, for
the keys (`key=ctrl+t`, `debug_key=ctrl+T`) and the paths `install-mpv-script` writes.

## Troubleshooting

- **Nothing happens on Ctrl+T:** check that `live-translate.lua` is in mpv's `scripts` folder
  (`%APPDATA%\mpv\scripts` on Windows, or `portable_config\scripts`). Another script or your
  `input.conf` may also use Ctrl+T; change `key=` in `live_translate.conf`.
- **"could not run 'live-translate'":** run `live-translate install-mpv-script` again so the script
  knows where the command is.
- **"the link stopped working":** the link expired. Copy a fresh one and open it again.
- **"Missing … API key" or a quota/credits message:** run `live-translate setup`, or top up the
  account it names.
- **Anything else:** turn on debug, reproduce it, and attach the log file to an issue.

## Privacy

The audio of the windows being processed is sent to ElevenLabs, and the transcribed text to
Anthropic. Nothing is sent until you press the key.

## Limitations

- Live streams and web pages (YouTube etc.) aren't supported, only files and direct links to them.
- If a subtitle file with the same name already exists and wasn't written by this tool, it is left
  alone and the new one goes into the cache folder instead (still loaded into mpv).
- Claude occasionally declines to translate a window. Those lines then appear only in the
  original-language track.

## Development

```sh
uv sync
uv run pytest
uv run live-translate generate some-video.mkv --fake   # whole pipeline, no API calls
```

`--fake` also works with `serve`, which is how the mpv integration was tested headlessly.

### Manual test checklist (Windows)

1. `uv tool install`, then `live-translate setup` with debug on.
2. Open a Japanese video in mpv, press Ctrl+T: "started", then subtitles within about 10 seconds.
3. Seek forward 10 minutes: subtitles catch up within a few seconds.
4. Pause for 3 minutes: the cost on screen stops rising.
5. Press Ctrl+T: "stopped · $x.xx".
6. Reopen the file: the `.srt` files load on their own, and Ctrl+T doesn't add duplicate tracks.

## License

MIT
