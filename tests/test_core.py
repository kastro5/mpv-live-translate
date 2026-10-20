import pytest

from live_translate import config as cfg
from live_translate import langs
from live_translate.cache import Cache
from live_translate.cues import CJK, LATIN, build_cues, format_srt, join_cues
from live_translate.output import choose_path, write_atomic
from live_translate.scheduler import to_start, wanted, window_index
from live_translate.translate import build_prompt, check_translations
from live_translate.windows import (
    Window,
    make_windows,
    nominal_boundaries,
    owned_words,
    parse_silences,
    pick_seam,
)


def words(*specs):
    """specs: (text, start, end) -> Scribe-style tokens with spacing between words."""
    out = []
    for text, start, end in specs:
        if out:
            out.append({"text": " ", "start": out[-1]["end"], "end": start, "type": "spacing"})
        out.append({"text": text, "start": start, "end": end, "type": "word"})
    return out


# --- cues -------------------------------------------------------------------------------


def test_silence_gap_splits_cues():
    cues = build_cues(words(("Hello", 0.0, 0.5), ("there", 0.6, 1.0), ("again", 3.0, 3.5)))
    assert [c[2] for c in cues] == ["Hello there", "again"]


def test_sentence_end_splits_once_enough_text():
    toks = words(("This", 0, 0.2), ("is", 0.3, 0.4), ("a", 0.5, 0.6), ("long", 0.65, 0.7), ("sentence.", 0.7, 1.0), ("Next", 1.1, 1.3))
    assert [c[2] for c in build_cues(toks)] == ["This is a long sentence.", "Next"]
    short = words(("Hi", 0, 0.2), ("there.", 0.3, 0.5), ("Next", 0.6, 0.8))
    assert [c[2] for c in build_cues(short)] == ["Hi there. Next"]  # under min_sentence_chars


def test_audio_events_are_dropped():
    toks = words(("Hi", 0, 0.5))
    toks.append({"text": "(laughter)", "start": 0.6, "end": 1.0, "type": "audio_event"})
    assert [c[2] for c in build_cues(toks)] == ["Hi"]


def test_cjk_layout_wraps_narrower():
    assert CJK.line_chars < LATIN.line_chars


def test_join_cues_clamps_overlap_at_seams():
    joined = join_cues([[(0.0, 5.0, "a")], [(4.0, 6.0, "b")]])
    assert joined == [(0.0, 4.0, "a"), (4.0, 6.0, "b")]


def test_format_srt():
    assert format_srt([(1.5, 2.25, "hi")]) == "1\n00:00:01,500 --> 00:00:02,250\nhi\n"


# --- windows ----------------------------------------------------------------------------


def test_nominal_boundaries_join_short_tail():
    assert nominal_boundaries(150, 60) == [60]
    assert nominal_boundaries(185, 60) == [60, 120]
    assert nominal_boundaries(30, 60) == []


def test_parse_silences_offsets_and_open_end():
    stderr = "silence_start: 1.0\nsilence_end: 2.5 | silence_duration: 1.5\nsilence_start: 14\n"
    assert parse_silences(stderr, 52.0, 16.0) == [(53.0, 54.5), (66.0, 68.0)]


def test_pick_seam_longest_silence_within_search():
    assert pick_seam(60, [(55, 55.5), (61, 63), (80, 90)]) == 62
    assert pick_seam(60, [(59.9, 60.0)]) == 60  # too short: keep the boundary
    assert pick_seam(60, [(66, 75)]) == 67  # clipped to the search range [52, 68]


def test_windows_tile_timeline_and_own_words_once():
    ws = make_windows([59.0, 121.0], 150.0)
    assert [(w.start, w.end) for w in ws] == [(0.0, 59.0), (59.0, 121.0), (121.0, 150.0)]
    toks = words(("a", 57.0, 57.5), ("b", 59.5, 60.0), ("c", 120.9, 121.4))
    owned = [[t["text"] for t in owned_words(w, toks) if t["type"] == "word"] for w in ws]
    assert owned == [["a"], ["b", "c"], []]


# --- scheduler --------------------------------------------------------------------------

STARTS = [0, 60, 120, 180, 240]


def test_lookahead_covers_horizon_only():
    assert wanted(STARTS, 70, "lookahead", 120, set()) == [1, 2, 3]
    assert wanted(STARTS, 70, "lookahead", 120, {1}) == [2, 3]


def test_full_mode_continues_then_wraps():
    assert wanted(STARTS, 130, "full", 60, set()) == [2, 3, 4, 0, 1]


def test_to_start_respects_slots_and_running():
    assert to_start([1, 2, 3, 4], running={1: None}, slots=3) == [2, 3]
    assert window_index(STARTS, 59.9) == 0 and window_index(STARTS, 60) == 1


# --- translate --------------------------------------------------------------------------


def test_prompt_numbers_cues_and_marks_context():
    prompt = build_prompt([(0, 1, "こんにちは")], [((-3, -2, "前"), "Before"), ((-2, -1, "未"), None)])
    assert "<context>" in prompt and "-> Before" in prompt
    assert "1 [00:00:00,000] こんにちは" in prompt


def test_check_translations():
    assert check_translations([{"id": 1, "text": " Hi "}, {"id": 2, "text": "Yo"}], 2) == ["Hi", "Yo"]
    assert check_translations([{"id": 2, "text": "Hi"}], 1) is None
    assert check_translations([{"id": 1, "text": " "}], 1) is None


# --- langs / config ---------------------------------------------------------------------


def test_langs():
    assert langs.iso1("jpn") == "ja" and langs.iso3("ja") == "jpn" and langs.name("ja") == "Japanese"
    assert langs.iso1("xyz") == "xyz"


def test_config_file_then_env(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[keys]\nanthropic = "file"\n[live]\nmode = "full"\nlookahead_s = 180\n')
    config = cfg.load(path, env={"ANTHROPIC_API_KEY": "env"})
    assert config.anthropic_key == "env" and config.mode == "full" and config.lookahead_s == 180.0
    assert cfg.load(tmp_path / "missing.toml", env={}).model == "claude-sonnet-5-5"


def test_config_rejects_bad_values(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[live]\nmode = "sometimes"\n')
    with pytest.raises(cfg.ConfigError):
        cfg.load(path, env={})


# --- cache / output ---------------------------------------------------------------------


def test_cache_roundtrip(tmp_path):
    cache = Cache(tmp_path, "key")
    cache.set_seam(60, 60.0, 59.5)
    cache.set_transcript("scribe_v2", 0, 59.5, {"words": []})
    assert Cache(tmp_path, "key").seam(60, 60.0) == 59.5
    assert cache.transcript("scribe_v2", 0, 59.5) == {"words": []}
    assert cache.transcript("scribe_v2", 0, 60) is None


def test_output_never_overwrites_foreign_subtitles(tmp_path):
    video = tmp_path / "movie.mkv"
    video.write_bytes(b"")
    cache = Cache(tmp_path / "cache", "key")
    beside = tmp_path / "movie.en.srt"
    assert choose_path(video, "en", cache) == beside  # free: write beside the video

    write_atomic(beside, "ours")
    cache.record_output(beside, "ours")
    assert choose_path(video, "en", cache) == beside  # ours: keep using it

    beside.write_text("someone else's")
    assert choose_path(video, "en", cache) == cache.dir / "movie.en.srt"


# --- debug --------------------------------------------------------------------------------

from live_translate.debuglog import DebugLog, ass_panel, clock, ranges


def test_debug_log_keeps_recent_and_writes_all(tmp_path):
    log = DebugLog(tmp_path / "logs" / "x.log", keep=2)
    for n in range(3):
        log(f"event {n}", window=n)
    log.close()
    assert [e.split("  ", 1)[1] for e in log.events] == ["#2 event 1", "#3 event 2"]
    assert (tmp_path / "logs" / "x.log").read_text().count("event") == 3


def test_debug_helpers():
    assert ranges({0, 1, 2, 5}) == "1-3, 6" and ranges(set()) == "-"
    assert clock(751.4) == "12:31.4" and clock(3751) == "1:02:31.0"
    panel = ass_panel(["a {b} c\\d", "e"])
    assert panel.endswith("a (b) c/d\\Ne") and panel.startswith("{\\an7")
