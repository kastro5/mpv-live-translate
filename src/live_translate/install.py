"""`live-translate install-mpv-script`: put the Lua script in mpv's scripts folder and point it
at this installation (mpv started from Explorer/Finder often doesn't see the user's PATH)."""

import os
import shutil
import sys
from importlib import resources
from pathlib import Path

SCRIPT = "live-translate.lua"
OPTS = "live_translate.conf"


def mpv_config_dir():
    if os.name == "nt":
        return Path(os.environ["APPDATA"]) / "mpv"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "mpv"


def script_source():
    packaged = resources.files("live_translate") / SCRIPT
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    # Editable/dev install: the script lives in the repo's mpv/ folder
    return (Path(__file__).resolve().parents[2] / "mpv" / SCRIPT).read_text(encoding="utf-8")


def executable():
    found = shutil.which("live-translate")
    if found:
        return str(Path(found).resolve())
    return str(Path(sys.argv[0]).resolve())


def update_opts(path, values):
    """Set keys in a script-opts file, keeping anything else the user wrote."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    kept = [line for line in lines if line.split("=", 1)[0].strip() not in values]
    path.write_text("\n".join(kept + [f"{k}={v}" for k, v in values.items()]) + "\n", encoding="utf-8")


def install(mpv_dir=None, hint=True):
    mpv_dir = mpv_dir or mpv_config_dir()
    scripts, opts = mpv_dir / "scripts", mpv_dir / "script-opts"
    scripts.mkdir(parents=True, exist_ok=True)
    opts.mkdir(parents=True, exist_ok=True)

    (scripts / SCRIPT).write_text(script_source(), encoding="utf-8")
    values = {"executable": executable()}
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        values["ffmpeg"] = str(Path(ffmpeg).resolve())
    update_opts(opts / OPTS, values)

    print(f"Installed {scripts / SCRIPT}")
    print(f"Settings  {opts / OPTS}")
    if not ffmpeg:
        print("Warning: ffmpeg not found on PATH. Install it, then run this again (or set ffmpeg= in the file above).")
    if hint:
        print("Next: run `live-translate setup` if you haven't yet, then press Ctrl+T in mpv.")
