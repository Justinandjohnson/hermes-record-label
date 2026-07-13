"""Scan Ableton project folders for backup .als files and infer work sessions.

Ableton's auto-save names backups like
``MyProject [2025-05-14 142301].als``.  We parse the timestamp out of each
filename, sort, and group into "sessions" using a 2-hour idle threshold
between consecutive saves.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from session_intelligence.als_parser import ALSInfo, diff_als, parse_als

logger = logging.getLogger(__name__)

# Ableton backup naming: "ProjectName [YYYY-MM-DD HHMMSS].als"
BACKUP_FILENAME_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{6})\]\.als$")
BACKUP_TIMESTAMP_FMT = "%Y-%m-%d %H%M%S"

# Gap (between consecutive saves) that splits one session from the next.
SESSION_GAP = timedelta(hours=2)


@dataclass
class AbletonSession:
    """An inferred Ableton work session."""

    project_name: str
    project_path: str | None
    session_date: str
    started_at: datetime
    ended_at: datetime | None = None
    duration_minutes: float | None = None
    save_count: int = 0
    export_count: int = 0
    bpm: float | None = None
    time_sig_num: int | None = None
    time_sig_den: int | None = None
    musical_key: str | None = None
    track_count: int | None = None
    backups: list[tuple[datetime, Path]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_backup_timestamp(filename: str) -> datetime | None:
    m = BACKUP_FILENAME_RE.search(filename)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), BACKUP_TIMESTAMP_FMT)
    except ValueError:
        return None


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _group_by_gap(
    timestamps: list[tuple[datetime, Path]], gap: timedelta = SESSION_GAP
) -> list[list[tuple[datetime, Path]]]:
    """Split a sorted list of (timestamp, path) into session groups."""
    if not timestamps:
        return []
    groups: list[list[tuple[datetime, Path]]] = [[timestamps[0]]]
    for ts, path in timestamps[1:]:
        if ts - groups[-1][-1][0] >= gap:
            groups.append([(ts, path)])
        else:
            groups[-1].append((ts, path))
    return groups


def _info_to_session(
    project_name: str,
    project_path: Path,
    group: list[tuple[datetime, Path]],
    first_info: ALSInfo,
    last_info: ALSInfo,
) -> AbletonSession:
    started_at = group[0][0]
    ended_at = group[-1][0] if len(group) > 1 else None
    duration_minutes: float | None = None
    if ended_at is not None:
        duration_minutes = (ended_at - started_at).total_seconds() / 60.0

    return AbletonSession(
        project_name=project_name,
        project_path=str(project_path),
        session_date=started_at.strftime("%Y-%m-%d"),
        started_at=started_at,
        ended_at=ended_at,
        duration_minutes=duration_minutes,
        save_count=len(group),
        export_count=0,
        bpm=last_info.bpm if last_info.bpm is not None else first_info.bpm,
        time_sig_num=last_info.time_sig_num or first_info.time_sig_num,
        time_sig_den=last_info.time_sig_den or first_info.time_sig_den,
        musical_key=last_info.musical_key or first_info.musical_key,
        track_count=len(last_info.track_names) or len(first_info.track_names) or None,
        backups=group,
    )


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------


def _session_exists(conn: sqlite3.Connection, project_name: str, started_at: datetime) -> int | None:
    cur = conn.execute(
        "SELECT id FROM ableton_sessions WHERE project_name = ? AND started_at = ?",
        (project_name, started_at.isoformat(sep=" ")),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _insert_session(conn: sqlite3.Connection, s: AbletonSession) -> int:
    cur = conn.execute(
        """
        INSERT INTO ableton_sessions (
            project_name, project_path, session_date, started_at, ended_at,
            duration_minutes, save_count, export_count, bpm, time_sig_num,
            time_sig_den, musical_key, track_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            s.project_name,
            s.project_path,
            s.session_date,
            s.started_at.isoformat(sep=" "),
            s.ended_at.isoformat(sep=" ") if s.ended_at else None,
            s.duration_minutes,
            s.save_count,
            s.export_count,
            s.bpm,
            s.time_sig_num,
            s.time_sig_den,
            s.musical_key,
            s.track_count,
        ),
    )
    return int(cur.lastrowid or 0)


def _insert_version(
    conn: sqlite3.Connection,
    session_id: int,
    project_name: str,
    als_path: Path,
    saved_at: datetime,
    info: ALSInfo,
    diff: dict,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO project_versions (
            session_id, project_name, als_path, als_hash, saved_at,
            bpm, time_sig_num, time_sig_den, musical_key,
            track_names, plugin_names, diff_from_prev
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            project_name,
            str(als_path),
            info.als_hash,
            saved_at.isoformat(sep=" "),
            info.bpm,
            info.time_sig_num,
            info.time_sig_den,
            info.musical_key,
            json.dumps(info.track_names),
            json.dumps(info.plugin_names),
            json.dumps(diff),
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_project(project_dir: Path, db_path: str) -> list[AbletonSession]:
    """Scan an Ableton project folder; persist any new sessions found."""
    project_dir = Path(project_dir)
    if not project_dir.is_dir():
        logger.warning("Project dir does not exist: %s", project_dir)
        return []

    project_name = project_dir.name

    backup_dir = project_dir / "Backup"
    backups: list[tuple[datetime, Path]] = []
    if backup_dir.is_dir():
        for als in backup_dir.glob("*.als"):
            ts = _parse_backup_timestamp(als.name)
            if ts is not None:
                backups.append((ts, als))

    if not backups:
        logger.info("No backup .als files found in %s", project_dir)
        return []

    backups.sort(key=lambda t: t[0])
    groups = _group_by_gap(backups)

    sessions: list[AbletonSession] = []
    try:
        conn = _connect(db_path)
    except sqlite3.Error:
        logger.exception("Failed to open DB at %s", db_path)
        return []

    try:
        prev_info: ALSInfo | None = None
        with conn:
            for group in groups:
                first_path = group[0][1]
                last_path = group[-1][1]
                first_info = parse_als(first_path)
                last_info = parse_als(last_path) if last_path != first_path else first_info

                session = _info_to_session(
                    project_name, project_dir, group, first_info, last_info
                )

                if _session_exists(conn, session.project_name, session.started_at) is not None:
                    logger.debug(
                        "Session already in DB for %s @ %s",
                        session.project_name,
                        session.started_at,
                    )
                    prev_info = last_info
                    continue

                session_id = _insert_session(conn, session)

                # Insert versions
                for ts, path in group:
                    info = (
                        first_info
                        if path == first_path
                        else last_info
                        if path == last_path
                        else parse_als(path)
                    )
                    diff = diff_als(prev_info, info)
                    _insert_version(
                        conn, session_id, project_name, path, ts, info, diff
                    )
                    prev_info = info

                sessions.append(session)
    finally:
        conn.close()

    return sessions
