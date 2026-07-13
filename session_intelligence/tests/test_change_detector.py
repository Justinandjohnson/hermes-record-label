"""Tests for change_detector hash-based logic."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from session_intelligence.change_detector import analyze_export, get_prev_export


def test_get_prev_export_none(fresh_db: str) -> None:
    assert get_prev_export("anything", fresh_db) is None


def test_analyze_export_first_export(fresh_db: str, tmp_path: Path) -> None:
    f = tmp_path / "MyProject Project" / "MyProject_bounce.wav"
    f.parent.mkdir()
    f.write_bytes(b"hello world audio bytes")

    info = analyze_export(f, fresh_db)
    assert info.file_hash is not None
    # First export → no previous, so changed_from_prev should be None or False.
    assert info.changed_from_prev in (False, None)
    assert info.file_size == len(b"hello world audio bytes")

    conn = sqlite3.connect(fresh_db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0]
    finally:
        conn.close()
    assert n == 1


def test_analyze_export_identical_file_not_changed(fresh_db: str, tmp_path: Path) -> None:
    project = tmp_path / "MyProject Project"
    project.mkdir()
    first = project / "bounce_v1.wav"
    second = project / "bounce_v2.wav"
    payload = b"identical bytes" * 100
    first.write_bytes(payload)
    second.write_bytes(payload)

    info1 = analyze_export(first, fresh_db)
    assert info1.file_hash is not None

    info2 = analyze_export(second, fresh_db)
    assert info2.file_hash == info1.file_hash
    assert info2.changed_from_prev is False
    assert info2.similarity_score == 1.0


def test_analyze_export_different_file_is_changed(fresh_db: str, tmp_path: Path) -> None:
    project = tmp_path / "MyProject Project"
    project.mkdir()
    first = project / "bounce_v1.wav"
    second = project / "bounce_v2.wav"
    first.write_bytes(b"version one audio")
    second.write_bytes(b"version TWO audio, totally different")

    info1 = analyze_export(first, fresh_db)
    info2 = analyze_export(second, fresh_db)
    assert info2.file_hash != info1.file_hash
    assert info2.changed_from_prev is True


def test_get_prev_export_returns_latest(fresh_db: str, tmp_path: Path) -> None:
    project = tmp_path / "FooBar Project"
    project.mkdir()
    f1 = project / "a.wav"
    f1.write_bytes(b"aaa")
    analyze_export(f1, fresh_db)

    f2 = project / "b.wav"
    f2.write_bytes(b"bbb")
    analyze_export(f2, fresh_db)

    prev = get_prev_export("FooBar", fresh_db)
    assert prev is not None
    assert prev.file_path == str(f2)
