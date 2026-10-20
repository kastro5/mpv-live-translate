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

You need [mpv](https://mpv.io/installation/), [ffmpeg](https://ffmpeg.org/download.html) on your
PATH, [uv](https://docs.astral.sh/uv/getting-started/installation/), and API keys for
[ElevenLabs](https://elevenlabs.io/app/settings/api-keys) and
[Anthropic](https://platform.claude.com/settings/keys).

```sh
uv tool install git+https://github.com/kastro5/mpv-live-translate
live-translate install-mpv-script   # copies the script into mpv and records where things are
live-translate config               # creates config.toml and prints its path: put your keys there
```

The keys can instead come from the `ELEVENLABS_API_KEY` and `ANTHROPIC_API_KEY` environment
variables, which win over the file. On Windows the config file is
`%APPDATA%\live-translate\config.toml`; on Linux `~/.config/live-translate/config.toml`; on macOS
`~/Library/Application Support/live-translate/config.toml`.

Updating: `uv tool upgrade mpv-live-translate`, then run `live-translate install-mpv-script` again.

## Use

Open a video in mpv and press <kbd>Ctrl</kbd>+<kbd>T</kbd>. Press it again to stop.

- The translation is selected automatically. Cycle subtitle tracks with <kbd>j</kbd> as usual.
- To show the original as a second line, add `ctrl+j cycle secondary-sid` to mpv's `input.conf`.
- To change the key, set `key=...` in `script-opts/live_translate.conf`, or bind
  `script-binding live_translate/toggle` in `input.conf`.

Whole files can also be done ahead of time without mpv:

```sh
live-translate generate movie.mkv
```

That fills the same cache, so playing the file afterwards is instant and free.

## Settings

`config.toml`:

```toml
[transcribe]
source_language = "auto"   # or e.g. "ja" to skip detection

[translate]
target = "en"              # any language Claude writes
model = "claude-sonnet-5-5"
effort = "medium"          # low | medium | high | xhigh | max

[live]
mode = "lookahead"         # lookahead: stay ahead of the playhead; full: then do the rest of the file
lookahead_s = 120
window_s = 60
```

## Privacy

The audio of the windows being processed is sent to ElevenLabs, and the transcribed text to
Anthropic. Nothing is sent until you press the key.

## Limitations

- Local files only (no streams or URLs yet).
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

1. `uv tool install`, `install-mpv-script`, `config` with keys.
2. Open a Japanese video in mpv, press Ctrl+T: "started", then subtitles within about 20 seconds.
3. Seek forward 10 minutes: subtitles catch up within a few seconds.
4. Pause for 3 minutes: the cost on screen stops rising.
5. Press Ctrl+T: "stopped · $x.xx".
6. Reopen the file: the `.srt` files load on their own, and Ctrl+T doesn't add duplicate tracks.

## License

MIT
