"""Tests for session_tracker grouping + scan logic."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from session_intelligence.session_tracker import (
    BACKUP_FILENAME_RE,
    _group_by_gap,
    _parse_backup_timestamp,
    scan_project,
)


def test_backup_filename_regex_matches() -> None:
    m = BACKUP_FILENAME_RE.search("MyProject [2025-05-14 142301].als")
    assert m is not None
    assert m.group(1) == "2025-05-14 142301"


def test_backup_filename_regex_rejects_garbage() -> None:
    assert BACKUP_FILENAME_RE.search("MyProject.als") is None
    assert BACKUP_FILENAME_RE.search("backup-2025-05-14.als") is None


def test_parse_backup_timestamp() -> None:
    ts = _parse_backup_timestamp("MyProject [2025-05-14 142301].als")
    assert ts == datetime(2025, 5, 14, 14, 23, 1)
    assert _parse_backup_timestamp("nope.als") is None


def test_group_by_gap_splits_on_two_hour_gap() -> None:
    base = datetime(2025, 5, 14, 10, 0, 0)
    items = [
        (base, Path("a.als")),
        (base + timedelta(minutes=10), Path("b.als")),
        (base + timedelta(minutes=20), Path("c.als")),
        # Two-hour gap → new session
        (base + timedelta(hours=2, minutes=30), Path("d.als")),
        (base + timedelta(hours=2, minutes=45), Path("e.als")),
    ]
    groups = _group_by_gap(items)
    assert len(groups) == 2
    assert [len(g) for g in groups] == [3, 2]


def test_group_by_gap_empty() -> None:
    assert _group_by_gap([]) == []


def test_scan_project_persists_sessions(fresh_db: str, make_als, tmp_path: Path) -> None:
    project_dir = tmp_path / "MyProject Project"
    backup_dir = project_dir / "Backup"
    backup_dir.mkdir(parents=True)

    # Three backups inside a 30 min window → 1 session.
    template = make_als(name="template.als", bpm=120, track_names=["Kick"])
    for stamp in ["2025-05-14 100000", "2025-05-14 101500", "2025-05-14 103000"]:
        shutil.copyfile(template, backup_dir / f"MyProject [{stamp}].als")

    # Two backups 3 hours later → new session.
    template2 = make_als(name="template2.als", bpm=128, track_names=["Kick", "Snare"])
    for stamp in ["2025-05-14 140000", "2025-05-14 141500"]:
        shutil.copyfile(template2, backup_dir / f"MyProject [{stamp}].als")

    sessions = scan_project(project_dir, fresh_db)
    assert len(sessions) == 2

    conn = sqlite3.connect(fresh_db)
    try:
        n_sessions = conn.execute("SELECT COUNT(*) FROM ableton_sessions").fetchone()[0]
        n_versions = conn.execute("SELECT COUNT(*) FROM project_versions").fetchone()[0]
    finally:
        conn.close()
    assert n_sessions == 2
    assert n_versions == 5

    # Second call must be idempotent.
    sessions2 = scan_project(project_dir, fresh_db)
    assert sessions2 == []
    conn = sqlite3.connect(fresh_db)
    try:
        n_sessions2 = conn.execute("SELECT COUNT(*) FROM ableton_sessions").fetchone()[0]
    finally:
        conn.close()
    assert n_sessions2 == 2


def test_scan_project_missing_dir(fresh_db: str, tmp_path: Path) -> None:
    assert scan_project(tmp_path / "nope", fresh_db) == []
