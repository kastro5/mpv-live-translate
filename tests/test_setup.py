import subprocess
import types

import pytest

from live_translate import config as cfg
from live_translate import install
from live_translate.setup import run_setup


def scripted(answers):
    answers = iter(answers)
    return lambda prompt="": next(answers)


def test_setup_writes_config_and_retries_bad_keys(tmp_path):
    path = tmp_path / "config.toml"
    installed, said, shortcuts = [], [], []
    run_setup(
        ask=scripted(["es", "2", "y", "y", "y"]),  # language, model (Opus), debug, install, shortcut
        ask_secret=scripted(["bad", "el-good", "an-good"]),
        say=said.append,
        checks=(lambda k: None if k == "el-good" else "ElevenLabs rejected this key", lambda k: None),
        path=path,
        mpv_dir=tmp_path / "mpv",
        do_install=lambda mpv_dir, hint: installed.append(mpv_dir),
        make_shortcut=lambda: shortcuts.append(True),
    )
    config = cfg.load(path, env={})
    assert shortcuts == [True]
    assert (config.elevenlabs_key, config.anthropic_key) == ("el-good", "an-good")
    assert (config.target, config.model, config.debug) == ("es", "claude-opus-5-5", True)
    assert installed == [tmp_path / "mpv"]
    assert any("rejected" in line for line in said)


def test_setup_enter_keeps_existing_values(tmp_path):
    path = tmp_path / "config.toml"
    cfg.save(cfg.Config(elevenlabs_key="el-old", anthropic_key="an-old", target="pt", model="claude-haiku-5-5"), path)
    run_setup(
        ask=scripted(["", "", "", "n"]),
        ask_secret=scripted(["", ""]),
        say=lambda line: None,
        checks=(lambda k: None, lambda k: None),
        path=path,
        mpv_dir=tmp_path / "mpv",
        do_install=lambda mpv_dir, hint: pytest.fail("should not install"),
    )
    config = cfg.load(path, env={})
    assert (config.elevenlabs_key, config.target, config.model, config.debug) == ("el-old", "pt", "claude-haiku-5-5", False)


def test_render_roundtrips_and_escapes(tmp_path):
    original = cfg.Config(anthropic_key='we"ird\\key', lookahead_s=180.0, debug=True)
    path = cfg.save(original, tmp_path / "config.toml")
    assert cfg.load(path, env={}) == original


def test_debug_must_be_boolean(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[live]\ndebug = "yes"\n')
    with pytest.raises(cfg.ConfigError):
        cfg.load(path, env={})


def test_shortcut_runs_powershell_with_paths_in_env(tmp_path, monkeypatch, capsys):
    mpv = tmp_path / "mpv.exe"
    mpv.write_bytes(b"")
    calls = []

    def fake_run(args, env, **kw):
        calls.append((args, env))
        return subprocess.CompletedProcess(args, 0, stdout="C:\\Users\\me\\Desktop\\x.lnk\n", stderr="")

    monkeypatch.setattr(install, "os", types.SimpleNamespace(name="nt", environ={"PATH": ""}))
    monkeypatch.setattr(install.subprocess, "run", fake_run)
    install.shortcut(str(mpv))
    (args, env), = calls
    assert args[0] == "powershell" and "CreateShortcut" in args[-1]
    assert env["LT_MPV"] == str(mpv.resolve()) and "live_translate-clipboard=yes" in env["LT_ARGS"]
    assert "Created" in capsys.readouterr().out
