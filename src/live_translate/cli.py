"""live-translate command line: generate, serve (started by the mpv script), config, install-mpv-script."""

import argparse
import asyncio
import dataclasses
import sys
import time
from pathlib import Path

from . import config as cfg
from .audio import AudioError, Ffmpeg
from .session import FatalError, Session


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

    session = Session(
        args.file, config, transcriber, translator, Ffmpeg(args.ffmpeg), cfg.cache_dir(),
        on_update=progress, on_message=lambda text: print(f"  {text}"),
    )
    await session.prepare()
    print(f"{args.file}: {session.duration / 60:.1f} min, {len(session.starts)} windows, {len(session.results)} cached")
    await session.run(until_complete=True)
    for kind, path in session.outputs.items():
        print(f"{kind}: {path}")
    print(f"Total ${session.cost:.4f} in {time.perf_counter() - started:.0f}s")


def show_config(args):
    path = cfg.config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cfg.TEMPLATE, encoding="utf-8")
        print(f"Created {path}")
    else:
        print(path)
    print(f"Cache: {cfg.cache_dir()}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="live-translate")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--target", help="target language code (default from config: en)")
        p.add_argument("--model", help="Claude model (default from config)")
        p.add_argument("--effort", choices=cfg.EFFORTS)
        p.add_argument("--ffmpeg", help="path to ffmpeg if it isn't on PATH")
        p.add_argument("--fake", action="store_true", help="fake providers: no API calls, for testing")

    p = sub.add_parser("generate", help="write subtitles for a whole file")
    p.add_argument("file", type=Path)
    common(p)

    p = sub.add_parser("serve", help="follow an mpv player (started by live-translate.lua)")
    p.add_argument("--ipc", required=True, help="mpv input-ipc-server path")
    p.add_argument("--file", required=True, type=Path)
    common(p)

    sub.add_parser("config", help="show (and create) the config file")

    p = sub.add_parser("install-mpv-script", help="copy live-translate.lua into mpv's scripts folder")
    p.add_argument("--mpv-dir", type=Path, help="mpv config dir (default: the usual one for this OS)")

    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            asyncio.run(generate(args))
        elif args.command == "serve":
            from .serve import serve

            asyncio.run(serve(args, load_config(args), providers))
        elif args.command == "config":
            show_config(args)
        elif args.command == "install-mpv-script":
            from .install import install

            install(args.mpv_dir)
    except (cfg.ConfigError, AudioError, FatalError) as e:
        sys.exit(f"live-translate: {e}")
    except KeyboardInterrupt:
        sys.exit(130)
