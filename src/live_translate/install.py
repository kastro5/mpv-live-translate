"""`live-translate install-mpv-script`: put the Lua script in mpv's scripts folder and point it
at this installation (mpv started from Explorer/Finder often doesn't see the user's PATH)."""

import os
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

from .config import ConfigError

SCRIPT = "live-translate.lua"
OPTS = "live_translate.conf"
SHORTCUT = "Translate copied link"
MPV_ARGS = "--player-operation-mode=pseudo-gui --script-opts-append=live_translate-clipboard=yes"
# Paths go in through the environment so nothing needs PowerShell quoting
_MAKE_LNK = """
$path = Join-Path ([Environment]::GetFolderPath('Desktop')) ($env:LT_NAME + '.lnk')
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
$lnk.TargetPath = $env:LT_MPV
$lnk.Arguments = $env:LT_ARGS
$lnk.WorkingDirectory = Split-Path $env:LT_MPV
$lnk.IconLocation = $env:LT_MPV + ',0'
$lnk.Description = 'Play the copied link in mpv with live-translate'
$lnk.Save()
$path
"""


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


def shortcut(mpv=None):
    """Desktop shortcut: mpv opens the link on the clipboard and starts translating."""
    found = mpv or shutil.which("mpv")
    if os.name != "nt":
        print(f"Desktop shortcuts are only made on Windows. The equivalent command:\n  mpv {MPV_ARGS}")
        return
    if not found:
        raise ConfigError("mpv.exe not found on PATH. Pass its location: live-translate shortcut --mpv <path>")
    exe = str(Path(found).resolve())
    env = {**os.environ, "LT_NAME": SHORTCUT, "LT_MPV": exe, "LT_ARGS": MPV_ARGS}
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _MAKE_LNK],
        env=env, capture_output=True, text=True,
    )
    if done.returncode != 0:
        raise ConfigError(f"could not create the shortcut: {done.stderr.strip()}")
    print(f"Created {done.stdout.strip()}")
    print("Copy a video link (PikPak: Copy link), then open the shortcut.")
