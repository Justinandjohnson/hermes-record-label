"""
End-to-end pipeline tests.

Covers the full track-detected → DB → fingerprint → tag → calendar path
without any real network or Google API calls.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from session_intelligence.watcher_integration import (
    SessionIntelligenceEmitter,
    _project_name_from_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _emit_new_track(emitter: SessionIntelligenceEmitter, path: Path, version: int = 1) -> None:
    emitter(
        "new_track_detected",
        {
            "track_id": 1,
            "file_path": str(path),
            "version": version,
            "parent_track_id": None,
            "title": path.stem,
        },
    )


# ---------------------------------------------------------------------------
# Project name detection
# ---------------------------------------------------------------------------

class TestProjectNameFromPath:
    def test_ableton_project_folder_stripped(self, tmp_path: Path) -> None:
        f = tmp_path / "Late Night Drive Project" / "bounce.wav"
        assert _project_name_from_path(f) == "Late Night Drive"

    def test_non_project_folder_returned_as_is(self, tmp_path: Path) -> None:
        f = tmp_path / "MyMixes" / "track.wav"
        assert _project_name_from_path(f) == "MyMixes"

    def test_non_project_parent_returns_parent_name(self, tmp_path: Path) -> None:
        """When the parent folder has no project suffix, the parent dir name is returned."""
        f = tmp_path / "bounce.wav"
        # _project_name_from_path returns the parent directory's name
        assert _project_name_from_path(f) == tmp_path.name


# ---------------------------------------------------------------------------
# Full pipeline: file → DB → export_event → calendar
# ---------------------------------------------------------------------------

class TestPipelineEndToEnd:
    """
    Exercises SessionIntelligenceEmitter with real DB and real audio files,
    but mocks out Google Calendar API and acoustid fingerprinting where needed.
    """

    @patch("session_intelligence.watcher_integration.create_export_event", return_value="https://calendar.google.com/test-event")
    def test_new_track_registers_export_event(
        self, mock_calendar, fresh_db: str, project_wav: Path
    ) -> None:
        """A new audio file produces an export_event row in the DB."""
        emitter = SessionIntelligenceEmitter(db_path=fresh_db)
        _emit_new_track(emitter, project_wav)

        conn = sqlite3.connect(fresh_db)
        rows = conn.execute("SELECT project_name, changed_from_prev FROM export_events").fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0][0] == "Late Night Drive"

    @patch("session_intelligence.watcher_integration.create_export_event", return_value="https://calendar.google.com/test-event")
    def test_calendar_event_fired_once(
        self, mock_calendar, fresh_db: str, project_wav: Path
    ) -> None:
        """Calendar integration is called exactly once per new export."""
        emitter = SessionIntelligenceEmitter(db_path=fresh_db)
        _emit_new_track(emitter, project_wav)
        mock_calendar.assert_called_once()

    @patch("session_intelligence.watcher_integration.create_export_event", return_value="https://calendar.google.com/test-event")
    def test_calendar_event_has_correct_project(
        self, mock_calendar, fresh_db: str, project_wav: Path
    ) -> None:
        """Calendar event is created with the correct project_name."""
        emitter = SessionIntelligenceEmitter(db_path=fresh_db)
        _emit_new_track(emitter, project_wav, version=3)
        call_kwargs = mock_calendar.call_args.kwargs
        assert call_kwargs["project_name"] == "Late Night Drive"
        assert call_kwargs["version"] == 3

    @patch("session_intelligence.watcher_integration.create_export_event", return_value=None)
    def test_pipeline_survives_calendar_failure(
        self, mock_calendar, fresh_db: str, project_wav: Path
    ) -> None:
        """If the calendar call fails, the pipeline still writes the export_event."""
        mock_calendar.side_effect = RuntimeError("network timeout")
        emitter = SessionIntelligenceEmitter(db_path=fresh_db)
        _emit_new_track(emitter, project_wav)  # should not raise

        conn = sqlite3.connect(fresh_db)
        count = conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0]
        conn.close()
        assert count == 1

    @patch("session_intelligence.watcher_integration.create_export_event", return_value=None)
    def test_non_existent_file_is_skipped(
        self, mock_calendar, fresh_db: str, tmp_path: Path
    ) -> None:
        """Payload with a missing file path is silently skipped."""
        emitter = SessionIntelligenceEmitter(db_path=fresh_db)
        emitter("new_track_detected", {"file_path": str(tmp_path / "ghost.wav")})
        mock_calendar.assert_not_called()

    def test_unknown_event_is_ignored(self, fresh_db: str, project_wav: Path) -> None:
        """Unrecognised event names are silently ignored."""
        emitter = SessionIntelligenceEmitter(db_path=fresh_db)
        emitter("something_else", {"file_path": str(project_wav)})  # must not raise

    @patch("session_intelligence.watcher_integration.create_export_event", return_value="https://calendar.google.com/test-event")
    def test_second_identical_export_is_not_changed(
        self, mock_calendar, fresh_db: str, make_wav
    ) -> None:
        """Two exports with identical bytes → changed_from_prev=False on second."""
        payload = b"same bytes " * 500
        f1 = make_wav("v1.wav")
        f2 = make_wav("v2.wav")
        # Overwrite both with identical content
        f1.write_bytes(payload)
        f2.write_bytes(payload)

        emitter = SessionIntelligenceEmitter(db_path=fresh_db)
        _emit_new_track(emitter, f1, version=1)
        _emit_new_track(emitter, f2, version=2)

        conn = sqlite3.connect(fresh_db)
        rows = conn.execute(
            "SELECT changed_from_prev FROM export_events ORDER BY id"
        ).fetchall()
        conn.close()

        assert rows[0][0] == 0  # first — no previous
        assert rows[1][0] == 0  # second — same hash, not changed

    @patch("session_intelligence.watcher_integration.create_export_event", return_value="https://calendar.google.com/test-event")
    def test_second_different_export_is_changed(
        self, mock_calendar, fresh_db: str, make_wav
    ) -> None:
        """Two exports with different bytes → changed_from_prev=True on second."""
        f1 = make_wav("v1.wav", freq=440.0)
        f2 = make_wav("v2.wav", freq=880.0)

        emitter = SessionIntelligenceEmitter(db_path=fresh_db)
        _emit_new_track(emitter, f1, version=1)
        _emit_new_track(emitter, f2, version=2)

        conn = sqlite3.connect(fresh_db)
        rows = conn.execute(
            "SELECT changed_from_prev FROM export_events ORDER BY id"
        ).fetchall()
        conn.close()

        assert rows[1][0] == 1  # second export IS changed
