"""`live-translate setup`: guided first run. Asks for keys (checked with free API calls),
target language and model, writes config.toml and installs the mpv script."""

import dataclasses
import getpass
import os
import shutil
import sys

from . import config as cfg
from . import langs
from .install import install, mpv_config_dir, shortcut

SCRIBE_PER_HOUR = 0.23
MODELS = [  # (model, label, translation $/hour, measured on Japanese with Sonnet and scaled by price)
    ("claude-sonnet-5-5", "Sonnet 5.5  recommended: close to Opus quality", 0.50),
    ("claude-opus-5-5", "Opus 5.5    best quality", 1.00),
    ("claude-haiku-5-5", "Haiku 5.5   cheapest, noticeably weaker", 0.03),
]


def check_elevenlabs(key):
    """None if the key works, else the reason. Keys limited to speech-to-text can't read the
    user endpoint; that refusal still proves the key itself is valid."""
    from elevenlabs.client import ElevenLabs

    try:
        ElevenLabs(api_key=key).user.get()
    except Exception as e:
        body = getattr(e, "body", None)
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(detail, dict) and detail.get("status") == "missing_permissions":
            return None
        if getattr(e, "status_code", None) == 401:
            return "ElevenLabs rejected this key"
        return f"could not reach ElevenLabs: {e}"
    return None


def check_anthropic(key):
    import anthropic

    try:
        anthropic.Anthropic(api_key=key).models.list(limit=1)
    except anthropic.AuthenticationError:
        return "Anthropic rejected this key"
    except Exception as e:
        return f"could not reach Anthropic: {e}"
    return None


def _masked(key):
    return f"...{key[-4:]}" if len(key) > 8 else "set"


def ask_key(name, current, check, ask_secret, say):
    while True:
        hint = f" [Enter keeps {_masked(current)}]" if current else ""
        key = ask_secret(f"{name} API key{hint}: ").strip() or current
        if not key:
            say(f"  A {name} key is required.")
            continue
        problem = check(key)
        if problem is None:
            say(f"  {name} key OK")
            return key
        say(f"  {problem}. Try again.")


def ask_choice(prompt, options, default, ask):
    while True:
        answer = ask(f"{prompt} [{default + 1}]: ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1


def ask_yes(prompt, default, ask):
    answer = ask(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not answer else answer.startswith("y")


def run_setup(ask=input, ask_secret=getpass.getpass, say=print, checks=(check_elevenlabs, check_anthropic),
              path=None, mpv_dir=None, do_install=install, make_shortcut=None):
    path = path or cfg.config_path()
    try:
        current = cfg.load(path, env={})
    except cfg.ConfigError as e:
        say(f"Existing config has a problem ({e}); starting from defaults.")
        current = cfg.Config()

    say("live-translate setup. Press Enter to keep the value in brackets.\n")
    say("Keys: https://elevenlabs.io/app/settings/api-keys and https://platform.claude.com/settings/keys")
    elevenlabs = ask_key("ElevenLabs", current.elevenlabs_key, checks[0], ask_secret, say)
    anthropic_key = ask_key("Anthropic", current.anthropic_key, checks[1], ask_secret, say)

    target = ask(f"\nTranslate into which language? (ISO code, e.g. en, es, pt) [{current.target}]: ").strip()
    target = target or current.target
    if langs.name(target) == target:
        say(f"  '{target}' isn't in the built-in list; Claude will be asked for '{target}' as is.")
    else:
        say(f"  {langs.name(target)}")

    say("\nTranslation model (cost per hour watched, including ~$%.2f for transcription):" % SCRIBE_PER_HOUR)
    for n, (model, label, per_hour) in enumerate(MODELS, 1):
        say(f"  {n}. {label}  ~${per_hour + SCRIBE_PER_HOUR:.2f}/h")
    names = [m for m, _, _ in MODELS]
    default = names.index(current.model) if current.model in names else 0
    model = names[ask_choice("Choose", MODELS, default, ask)]

    debug = ask_yes("\nShow the debug panel and write log files by default?", current.debug, ask)

    config = dataclasses.replace(
        current, elevenlabs_key=elevenlabs, anthropic_key=anthropic_key, target=target, model=model, debug=debug
    )
    saved = cfg.save(config, path)
    say(f"\nSaved {saved}")

    mpv_dir = mpv_dir or mpv_config_dir()
    say("\nFor a portable mpv (a portable_config folder next to mpv.exe), answer n and run")
    say("`live-translate install-mpv-script --mpv-dir <path to portable_config>` instead.")
    if ask_yes(f"Install or update the mpv script in {mpv_dir}?", True, ask):
        do_install(mpv_dir, hint=False)
    if make_shortcut and ask_yes("Create a desktop shortcut that plays a copied link with live-translate?", True, ask):
        try:
            make_shortcut()
        except cfg.ConfigError as e:
            say(f"  {e}")
    if not shutil.which("ffmpeg"):
        say("\nWarning: ffmpeg isn't on PATH. Install it (Windows: winget install Gyan.FFmpeg), then run setup again.")
    say("\nDone. Open a video in mpv and press Ctrl+T.")


def setup():
    if not sys.stdin.isatty():
        raise cfg.ConfigError("setup is interactive; run it in a terminal")
    run_setup(make_shortcut=shortcut if os.name == "nt" else None)
