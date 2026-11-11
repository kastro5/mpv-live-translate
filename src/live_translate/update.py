"""`transcript update`: upgrade the installed tool with uv, then reinstall the mpv script from the
new version so the Lua side always matches the Python side."""

import base64
import os
import shutil
import subprocess

from .config import ConfigError
from .install import executable

PACKAGE = "mpv-live-translate"  # the name `uv tool install` knows it by

# Windows can't replace a running .exe, and uv copies tool executables there, so the upgrade runs
# in its own window after this process (and any `transcript serve` started by mpv) has exited.
# Values come in through the environment so nothing needs PowerShell quoting.
_WINDOWS_SCRIPT = """
$parent = Get-Process -Id $env:LT_PARENT -ErrorAction SilentlyContinue
if ($parent -and $parent.Path -eq $env:LT_EXE) { $parent.WaitForExit() }
Wait-Process -Id $env:LT_SELF -ErrorAction SilentlyContinue
while (Get-Process | Where-Object { $_.Path -eq $env:LT_EXE }) {
    Write-Host 'Waiting for live-translate to stop: press Ctrl+T in mpv or close mpv...'
    Start-Sleep -Seconds 2
}
& $env:LT_UV tool upgrade $env:LT_PACKAGE
if ($LASTEXITCODE -eq 0) {
    if ($env:LT_MPV_DIR) { & $env:LT_EXE install-mpv-script --mpv-dir $env:LT_MPV_DIR }
    else { & $env:LT_EXE install-mpv-script }
}
if ($LASTEXITCODE -eq 0) { Write-Host "`nUpdated." } else { Write-Host "`nUpdate failed (see above)." }
Read-Host 'Press Enter to close'
"""


def commands(uv, exe, mpv_dir=None):
    reinstall = [exe, "install-mpv-script", *(["--mpv-dir", str(mpv_dir)] if mpv_dir else [])]
    return [uv, "tool", "upgrade", PACKAGE], reinstall


def update(mpv_dir=None, run=subprocess.run, popen=subprocess.Popen, windows=None):
    uv = shutil.which("uv")
    if not uv:
        raise ConfigError("uv not found on PATH. Install it from https://docs.astral.sh/uv/ and try again.")
    exe = executable()
    windows = os.name == "nt" if windows is None else windows

    if windows:
        env = {
            **os.environ, "LT_UV": uv, "LT_EXE": exe, "LT_PACKAGE": PACKAGE, "LT_MPV_DIR": str(mpv_dir or ""),
            "LT_SELF": str(os.getpid()), "LT_PARENT": str(os.getppid()),
        }
        encoded = base64.b64encode(_WINDOWS_SCRIPT.encode("utf-16-le")).decode()
        popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            env=env, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        print("Updating in a new window (it waits for mpv's live-translate to stop first).")
        return

    upgrade, reinstall = commands(uv, exe, mpv_dir)
    if run(upgrade).returncode != 0:
        raise ConfigError(f"`uv tool upgrade {PACKAGE}` failed (see above). Was it installed with `uv tool install`?")
    # A new process, so the mpv script comes from the version just installed
    if run(reinstall).returncode != 0:
        raise ConfigError("upgraded, but reinstalling the mpv script failed (see above)")
