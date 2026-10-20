"""live-translate command line: generate, serve (started by the mpv script), config, install-mpv-script."""

import argparse
import asyncio
import dataclasses
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config as cfg
from .audio import AudioError, Ffmpeg
from .debuglog import DebugLog
from .session import FatalError, Session
from .stream import LinkError, log_stem


def providers(config, fake):
    if fake:
        from .transcribe import FakeTranscriber
        from .translate import FakeTranslator

        return FakeTranscriber(), FakeTranslator()
    from .transcribe import ScribeTranscriber
    from .translate import ClaudeTranslator

    cfg.require_keys(config)
    return ScribeTranscriber(config.elevenlabs_key), ClaudeTranslator(config.anthropic_key, config.model, config.effort)


def load_config(args):
    config = cfg.load()
    overrides = {k: v for k, v in (("target", args.target), ("model", args.model), ("effort", args.effort)) if v}
    return dataclasses.replace(config, **overrides)


async def generate(args):
    config = dataclasses.replace(load_config(args), mode="full")
    transcriber, translator = providers(config, args.fake)
    started = time.perf_counter()

    def progress(session):
        done = len(session.results)
        print(f"  {done}/{len(session.starts)} windows  |  ${session.cost:.4f}  |  {time.perf_counter() - started:.0f}s")

    log = None
    if args.debug or config.debug:
        log = DebugLog(cfg.cache_dir() / "logs" / f"{log_stem(args.file)}-{time.strftime('%Y%m%d-%H%M%S')}.log", echo=print)
        print(f"Debug log: {log.path}")
    session = Session(
        args.file, config, transcriber, translator, Ffmpeg(args.ffmpeg), cfg.cache_dir(),
        on_update=progress, on_message=lambda text: print(f"  {text}"), debug=log,
    )
    await session.prepare()
    print(f"{session.name}: {session.duration / 60:.1f} min, {len(session.starts)} windows, {len(session.results)} cached")
    await session.run(until_complete=True)
    for kind, path in session.outputs.items():
        print(f"{kind}: {path}")
    print(f"Total ${session.cost:.4f} in {time.perf_counter() - started:.0f}s")


def open_in_editor(path):
    if os.name == "nt":
        subprocess.Popen(["notepad.exe", str(path)])  # .toml often has no default app on Windows
    elif sys.platform == "darwin":
        subprocess.run(["open", "-t", str(path)])
    else:
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or shutil.which("xdg-open")
        if not editor:
            raise cfg.ConfigError(f"No editor found; set $EDITOR or open {path} yourself")
        subprocess.run([*editor.split(), str(path)])


def show_config(args):
    path = cfg.config_path()
    if not path.exists():
        cfg.save(cfg.Config(), path)
        print(f"Created {path} (run `live-translate setup` to fill it in interactively)")
    else:
        print(path)
    print(f"Cache: {cfg.cache_dir()}")
    if args.edit:
        open_in_editor(path)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="live-translate")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--target", help="target language code (default from config: en)")
        p.add_argument("--model", help="Claude model (default from config)")
        p.add_argument("--effort", choices=cfg.EFFORTS)
        p.add_argument("--ffmpeg", help="path to ffmpeg if it isn't on PATH")
        p.add_argument("--fake", action="store_true", help="fake providers: no API calls, for testing")
        p.add_argument("--debug", action="store_true", help="show what each step is doing and write a log file")

    p = sub.add_parser("generate", help="write subtitles for a whole file")
    p.add_argument("file", help="video file or direct http(s) link")
    common(p)

    p = sub.add_parser("serve", help="follow an mpv player (started by live-translate.lua)")
    p.add_argument("--ipc", required=True, help="mpv input-ipc-server path")
    p.add_argument("--file", required=True, help="video file or direct http(s) link")
    common(p)

    sub.add_parser("setup", help="guided first-time setup: API keys, language, model, mpv script")

    p = sub.add_parser("config", help="show (and create) the config file")
    p.add_argument("--edit", action="store_true", help="open it in a text editor")

    p = sub.add_parser("install-mpv-script", help="copy live-translate.lua into mpv's scripts folder")
    p.add_argument("--mpv-dir", type=Path, help="mpv config dir (default: the usual one for this OS)")

    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            asyncio.run(generate(args))
        elif args.command == "serve":
            from .serve import serve

            asyncio.run(serve(args, load_config(args), providers))
        elif args.command == "setup":
            from .setup import setup

            setup()
        elif args.command == "config":
            show_config(args)
        elif args.command == "install-mpv-script":
            from .install import install

            install(args.mpv_dir)
    except (cfg.ConfigError, AudioError, LinkError, FatalError) as e:
        sys.exit(f"live-translate: {e}")
    except KeyboardInterrupt:
        sys.exit(130)
