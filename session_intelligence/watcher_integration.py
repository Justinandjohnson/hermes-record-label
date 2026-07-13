"""Integration glue between the file_watcher and session_intelligence.

Provides a callable that you can pass as the ``emit`` argument to
``FileWatcherService``. It listens for ``new_track_detected`` events and:

1. Runs change-detection on the new export.
2. Writes session metadata tags onto the audio file.
3. Links the export to the nearest ableton_session by time, if any.
4. Bumps ``ableton_sessions.export_count``.
5. Creates a Google Calendar event marking the export.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from session_intelligence._paths import default_db_path
from session_intelligence.calendar_sync import create_export_event
from session_intelligence.change_detector import ExportInfo, analyze_export
from session_intelligence.metadata_writer import write_export_metadata
from session_intelligence.session_tracker import scan_project

logger = logging.getLogger(__name__)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _find_nearest_session(
    conn: sqlite3.Connection, project_name: str, when: datetime
) -> tuple[int | None, float | None, str | None]:
    """Return (session_id, bpm, session_date) of the nearest session to *when*."""
    cur = conn.execute(
        """
        SELECT id, bpm, session_date, started_at
        FROM ableton_sessions
        WHERE project_name = ?
        ORDER BY ABS(strftime('%s', started_at) - strftime('%s', ?)) ASC
        LIMIT 1
        """,
        (project_name, when.isoformat(sep=" ")),
    )
    row = cur.fetchone()
    if row is None:
        return None, None, None
    return int(row[0]), row[1], row[2]


def _project_name_from_path(file_path: Path) -> str:
    name = file_path.parent.name
    if name.endswith(" Project"):
        name = name[: -len(" Project")]
    return name or file_path.stem


class SessionIntelligenceEmitter:
    """Hermes-event emitter that wires audio exports into the session DB."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        project_folder: str | Path | None = None,
    ) -> None:
        self.db_path = str(db_path) if db_path is not None else str(default_db_path())
        self.project_folder = Path(project_folder) if project_folder is not None else None

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    def __call__(self, event_name: str, payload: dict[str, Any]) -> None:
        try:
            if event_name == "new_track_detected":
                self._handle_new_export(payload)
        except Exception:
            logger.exception("SessionIntelligenceEmitter handler crashed")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _handle_new_export(self, payload: dict[str, Any]) -> None:
        raw_path = payload.get("file_path")
        if not raw_path:
            return
        path = Path(raw_path)
        if not path.is_file():
            logger.debug("Skipping non-existent export %s", path)
            return

        project_name = _project_name_from_path(path)
        when = datetime.now()

        # 1. Find the nearest session (if any).
        session_id: int | None = None
        bpm: float | None = None
        session_date = when.strftime("%Y-%m-%d")
        try:
            conn = _connect(self.db_path)
        except sqlite3.Error:
            logger.exception("Cannot open DB; export tagging skipped")
            return
        try:
            session_id, bpm, sdate = _find_nearest_session(conn, project_name, when)
            if sdate:
                session_date = sdate
        finally:
            conn.close()

        # 2. Run change detection (persists to export_events).
        info: ExportInfo = analyze_export(
            path, self.db_path, project_name=project_name, session_id=session_id
        )
        logger.info(
            "Export analyzed: %s changed=%s sim=%s",
            path.name,
            info.changed_from_prev,
            info.similarity_score,
        )

        # 3. Write tags to the audio file.
        write_export_metadata(path, project_name, bpm, session_date)

        # 4. Bump the session's export count.
        if session_id is not None:
            try:
                conn = _connect(self.db_path)
                with conn:
                    conn.execute(
                        "UPDATE ableton_sessions SET export_count = export_count + 1 WHERE id = ?",
                        (session_id,),
                    )
                conn.close()
            except sqlite3.Error:
                logger.exception("Failed to bump export_count for session %s", session_id)

        # 5. Create a Google Calendar event marking this export.
        try:
            create_export_event(
                project_name=project_name,
                file_path=path,
                bpm=bpm,
                version=payload.get("version", 1),
                changed=info.changed_from_prev,
                similarity=info.similarity_score,
                session_date=session_date,
                when=when,
            )
        except Exception:
            logger.exception("Calendar event creation failed for %s", path.name)

    # ------------------------------------------------------------------
    # Backfill helper
    # ------------------------------------------------------------------

    def scan_project_folder(self, project_folder: str | Path | None = None) -> int:
        """One-shot scan of an Ableton project folder for backup .als files."""
        folder = Path(project_folder) if project_folder is not None else self.project_folder
        if folder is None:
            logger.warning("No project_folder configured; skipping scan")
            return 0
        try:
            sessions = scan_project(folder, self.db_path)
        except Exception:
            logger.exception("scan_project failed for %s", folder)
            return 0
        logger.info("Scanned %s: %d new session(s)", folder, len(sessions))
        return len(sessions)
