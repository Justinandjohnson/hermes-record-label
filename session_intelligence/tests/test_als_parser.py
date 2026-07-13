"""Tests for the .als parser."""

from __future__ import annotations

from pathlib import Path

from session_intelligence.als_parser import ALSInfo, diff_als, parse_als


def test_parse_als_extracts_basic_metadata(make_als) -> None:
    path: Path = make_als(
        name="proj.als",
        bpm=140.0,
        time_sig_num=7,
        time_sig_den=8,
        track_names=["Kick", "Snare", "Pad"],
        plugins=["Serum", "ValhallaVintageVerb"],
        root_note=9,  # A
        scale_name="Minor",
    )

    info = parse_als(path)

    assert info.bpm == 140.0
    assert info.time_sig_num == 7
    assert info.time_sig_den == 8
    assert info.musical_key == "A Minor"
    assert set(info.track_names) == {"Kick", "Snare", "Pad"}
    assert set(info.plugin_names) >= {"Serum", "ValhallaVintageVerb"}
    assert info.als_hash is not None
    assert len(info.als_hash) == 32  # MD5 hex


def test_parse_als_handles_missing_file(tmp_path: Path) -> None:
    info = parse_als(tmp_path / "nope.als")
    assert isinstance(info, ALSInfo)
    assert info.bpm is None
    assert info.track_names == []


def test_parse_als_handles_garbage(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.als"
    bogus.write_bytes(b"not a gzip at all")
    info = parse_als(bogus)
    assert info.bpm is None


def test_diff_als_first_version(make_als) -> None:
    info = parse_als(make_als(track_names=["Kick"], plugins=["Serum"]))
    d = diff_als(None, info)
    assert d["added_tracks"] == ["Kick"]
    assert d["removed_tracks"] == []
    assert d["bpm_changed"] is True
    assert d["new_plugins"] == ["Serum"]


def test_diff_als_detects_changes(make_als) -> None:
    a = parse_als(make_als(name="a.als", bpm=120, track_names=["Kick"], plugins=["Serum"]))
    b = parse_als(
        make_als(
            name="b.als",
            bpm=130,
            track_names=["Kick", "Snare"],
            plugins=["Serum", "Massive"],
            root_note=2,
            scale_name="Dorian",
        )
    )
    d = diff_als(a, b)
    assert d["added_tracks"] == ["Snare"]
    assert d["removed_tracks"] == []
    assert d["bpm_changed"] is True
    assert d["new_plugins"] == ["Massive"]
    assert d["key_changed"] is True


def test_diff_als_no_change(make_als) -> None:
    common = dict(track_names=["Kick"], plugins=["Serum"], bpm=120, root_note=0, scale_name="Major")
    a = parse_als(make_als(name="a.als", **common))
    b = parse_als(make_als(name="b.als", **common))
    d = diff_als(a, b)
    assert d["added_tracks"] == []
    assert d["removed_tracks"] == []
    assert d["bpm_changed"] is False
    assert d["new_plugins"] == []
    assert d["key_changed"] is False
