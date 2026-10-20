"""User settings from config.toml in the user config dir; API key env vars override the file."""

import json
import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

import platformdirs

APP = "live-translate"
MODES = ("lookahead", "full")
EFFORTS = ("low", "medium", "high", "xhigh", "max")

TEMPLATE = """\
# live-translate settings. `live-translate setup` writes this file; edit it any time with
# `live-translate config --edit`. API keys can also come from the ELEVENLABS_API_KEY and
# ANTHROPIC_API_KEY environment variables, which take precedence over this file.

[keys]
elevenlabs = {elevenlabs_key}
anthropic = {anthropic_key}

[transcribe]
source_language = {source_language}   # "auto", or an ISO code such as "ja" to skip detection

[translate]
target = {target}
model = {model}   # claude-sonnet-5-5 | claude-opus-5-5 | claude-haiku-5-5
effort = {effort}          # low | medium | high | xhigh | max

[live]
mode = {mode}         # lookahead: stay ahead of the playhead; full: then do the rest of the file
lookahead_s = {lookahead_s:g}
window_s = {window_s:g}
debug = {debug}            # true: show the debug panel from the start and write log files
"""


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    elevenlabs_key: str = ""
    anthropic_key: str = ""
    source_language: str = "auto"
    target: str = "en"
    model: str = "claude-sonnet-5-5"
    effort: str = "medium"
    mode: str = "lookahead"
    lookahead_s: float = 120.0
    window_s: float = 60.0
    debug: bool = False


# config.toml (section, key) -> Config field
_FILE_KEYS = {
    ("keys", "elevenlabs"): "elevenlabs_key",
    ("keys", "anthropic"): "anthropic_key",
    ("transcribe", "source_language"): "source_language",
    ("translate", "target"): "target",
    ("translate", "model"): "model",
    ("translate", "effort"): "effort",
    ("live", "mode"): "mode",
    ("live", "lookahead_s"): "lookahead_s",
    ("live", "window_s"): "window_s",
    ("live", "debug"): "debug",
}
_ENV_KEYS = {"ELEVENLABS_API_KEY": "elevenlabs_key", "ANTHROPIC_API_KEY": "anthropic_key"}


def config_path():
    return Path(platformdirs.user_config_dir(APP, appauthor=False)) / "config.toml"


def cache_dir():
    return Path(platformdirs.user_cache_dir(APP, appauthor=False))


def load(path=None, env=None):
    path = path or config_path()
    env = os.environ if env is None else env
    values = {}
    if path.exists():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path}: {e}") from e
        for (section, key), field in _FILE_KEYS.items():
            if key in data.get(section, {}):
                values[field] = data[section][key]
    for var, field in _ENV_KEYS.items():
        if env.get(var):
            values[field] = env[var]

    types = {f.name: f.type for f in fields(Config)}
    for field, value in values.items():
        if types[field] is float and isinstance(value, int):
            values[field] = float(value)
    config = Config(**values)

    if config.mode not in MODES:
        raise ConfigError(f"live.mode must be one of {MODES}, not {config.mode!r}")
    if config.effort not in EFFORTS:
        raise ConfigError(f"translate.effort must be one of {EFFORTS}, not {config.effort!r}")
    if not isinstance(config.debug, bool):
        raise ConfigError("live.debug must be true or false")
    if config.window_s < 10 or config.lookahead_s < config.window_s:
        raise ConfigError("live.window_s must be >= 10 and live.lookahead_s >= live.window_s")
    return config


def render(config):
    """config.toml text for these settings (strings as TOML basic strings via JSON escaping)."""
    values = {f.name: getattr(config, f.name) for f in fields(Config)}
    for name, value in values.items():
        if isinstance(value, bool):
            values[name] = "true" if value else "false"
        elif isinstance(value, str):
            values[name] = json.dumps(value)
    return TEMPLATE.format(**values)


def save(config, path=None):
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(config), encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)  # it holds API keys
    return path


def require_keys(config):
    missing = [name for name, key in (("ElevenLabs", config.elevenlabs_key), ("Anthropic", config.anthropic_key)) if not key]
    if missing:
        raise ConfigError(f"Missing {' and '.join(missing)} API key. Run `live-translate setup` to add it.")
