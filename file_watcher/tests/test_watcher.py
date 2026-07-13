"""Tests for the file watcher service (mocked watchdog events)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from file_watcher.track_registry import ensure_schema
from file_watcher.watcher import (
    DEBOUNCE_WINDOW_SECONDS,
    SETTLE_DELAY_SECONDS,
    FileWatcherService,
    _AudioFileHandler,
)


# ---------------------------------------------------------------------------
# _AudioFileHandler unit tests
# ---------------------------------------------------------------------------


class TestAudioFileHandler:
    def test_records_created_event(self) -> None:
        handler = _AudioFileHandler()
        event = MagicMock()
        event.is_directory = False
        event.src_path = "/tmp/test.wav"
        # Simulate a FileCreatedEvent by calling the handler directly.
        handler._record_event(event.src_path)
        # Not yet settled (0 seconds elapsed).
        assert handler.drain_settled(now=time.monotonic()) == []

    def test_settles_after_delay(self) -> None:
        handler = _AudioFileHandler()
        handler._record_event("/tmp/test.wav")
        future = time.monotonic() + SETTLE_DELAY_SECONDS + 0.1
        settled = handler.drain_settled(now=future)
        assert "/tmp/test.wav" in settled

    def test_settled_removed_from_pending(self) -> None:
        handler = _AudioFileHandler()
        handler._record_event("/tmp/test.wav")
        future = time.monotonic() + SETTLE_DELAY_SECONDS + 0.1
        handler.drain_settled(now=future)
        # Second drain should be empty.
        assert handler.drain_settled(now=future + 1) == []

    def test_multiple_events_debounced(self) -> None:
        handler = _AudioFileHandler()
        handler._record_event("/tmp/test.wav")
        # Second event for same file resets the timer.
        later = time.monotonic() + 1.0
        handler._pending["/tmp/test.wav"] = later
        # At time later + SETTLE_DELAY + 0.1, it should settle.
        settled = handler.drain_settled(now=later + SETTLE_DELAY_SECONDS + 0.1)
        assert "/tmp/test.wav" in settled

    def test_different_files_tracked_independently(self) -> None:
        handler = _AudioFileHandler()
        t0 = time.monotonic()
        handler._pending["/tmp/a.wav"] = t0
        handler._pending["/tmp/b.wav"] = t0 + 10.0  # 10s later
        future_a = t0 + SETTLE_DELAY_SECONDS + 0.1
        settled = handler.drain_settled(now=future_a)
        assert "/tmp/a.wav" in settled
        assert "/tmp/b.wav" not in settled


# ---------------------------------------------------------------------------
# FileWatcherService integration tests
# ---------------------------------------------------------------------------


def _make_wav_bytes(unique: bytes = b"") -> bytes:
    """Create minimal WAV-like content."""
    header = b"RIFF\x00\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
    header += b"\x01\x00\x02\x00\x44\xac\x00\x00\x10\xb1\x02\x00"
    header += b"\x04\x00\x10\x00data\x00\x00\x00\x00"
    padding = max(0, 20_000 - len(header) - len(unique))
    return header + unique + b"\x00" * padding


class TestFileWatcherService:
    def test_start_stop(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()

        svc = FileWatcherService(watch_dir=watch_dir, db_path=db_path)
        svc.start()
        assert svc.is_running
        svc.stop()
        assert not svc.is_running

    def test_start_nonexistent_dir_raises(self, tmp_path: Path) -> None:
        svc = FileWatcherService(
            watch_dir=tmp_path / "nope",
            db_path=tmp_path / "test.db",
        )
        with pytest.raises(FileNotFoundError):
            svc.start()

    def test_process_file_creates_track(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()

        events: list[tuple[str, dict[str, Any]]] = []

        def capture_event(name: str, payload: dict[str, Any]) -> None:
            events.append((name, payload))

        svc = FileWatcherService(
            watch_dir=watch_dir,
            db_path=db_path,
            emit=capture_event,
        )
        svc.start()

        # Write a file into the watch directory.
        wav = watch_dir / "New Track.wav"
        wav.write_bytes(_make_wav_bytes(b"unique1"))

        # Wait for settle + processing.
        time.sleep((SETTLE_DELAY_SECONDS * 2) + 2.0)

        svc.stop()

        # Verify a track was registered.
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()
        assert row is not None and row[0] >= 1

        # Verify the event was emitted.
        assert len(events) >= 1
        assert events[0][0] == "new_track_detected"
        assert "track_id" in events[0][1]
        conn.close()

    def test_existing_file_is_processed_by_scan(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        (watch_dir / "Already Here.wav").write_bytes(_make_wav_bytes(b"prescan"))

        events: list[tuple[str, dict[str, Any]]] = []
        svc = FileWatcherService(
            watch_dir=watch_dir,
            db_path=db_path,
            emit=lambda n, p: events.append((n, p)),
            scan_interval=0.2,
        )
        svc.start()
        time.sleep(SETTLE_DELAY_SECONDS + 1.0)
        svc.stop()

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT title FROM tracks").fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "Already Here"
        assert [e[0] for e in events] == ["new_track_detected"]

    def test_duplicate_file_no_event(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()

        events: list[tuple[str, dict[str, Any]]] = []

        def capture_event(name: str, payload: dict[str, Any]) -> None:
            events.append((name, payload))

        svc = FileWatcherService(
            watch_dir=watch_dir,
            db_path=db_path,
            emit=capture_event,
        )
        svc.start()

        content = _make_wav_bytes(b"same_content")
        # Write the same content twice with different names.
        (watch_dir / "Song.wav").write_bytes(content)
        time.sleep((SETTLE_DELAY_SECONDS * 2) + 2.0)

        # Clear debounce so the second file can be processed.
        svc._last_processed.clear()
        (watch_dir / "Song copy.wav").write_bytes(content)
        time.sleep((SETTLE_DELAY_SECONDS * 2) + 2.0)

        svc.stop()

        # Only one event should fire (second file is a hash duplicate).
        track_events = [e for e in events if e[0] == "new_track_detected"]
        assert len(track_events) == 1

    def test_invalid_file_no_event(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()

        events: list[tuple[str, dict[str, Any]]] = []

        svc = FileWatcherService(
            watch_dir=watch_dir,
            db_path=db_path,
            emit=lambda n, p: events.append((n, p)),
        )
        svc.start()

        # Write a non-audio file.
        (watch_dir / "readme.txt").write_bytes(b"Hello world " * 1000)
        time.sleep(SETTLE_DELAY_SECONDS + 2.0)

        svc.stop()

        assert len(events) == 0

    def test_scan_waits_for_stable_file_size(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        wav = watch_dir / "Growing.wav"
        wav.write_bytes(_make_wav_bytes(b"first"))

        events: list[tuple[str, dict[str, Any]]] = []
        svc = FileWatcherService(
            watch_dir=watch_dir,
            db_path=db_path,
            emit=lambda n, p: events.append((n, p)),
            scan_interval=0.2,
        )
        svc.start()
        time.sleep(0.4)
        wav.write_bytes(_make_wav_bytes(b"second-larger"))
        time.sleep((SETTLE_DELAY_SECONDS * 2) + 1.0)
        svc.stop()

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT title FROM tracks").fetchall()
        conn.close()

        assert rows == [("Growing",)]
        assert [e[0] for e in events] == ["new_track_detected"]

    def test_post_registration_file_mutation_does_not_create_revision(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"
        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()

        wav = watch_dir / "Tagged.wav"
        wav.write_bytes(_make_wav_bytes(b"tagged"))
        events: list[tuple[str, dict[str, Any]]] = []

        def mutate_after_registration(name: str, payload: dict[str, Any]) -> None:
            events.append((name, payload))
            wav.write_bytes(_make_wav_bytes(b"tagged-after-metadata-write"))

        svc = FileWatcherService(
            watch_dir=watch_dir,
            db_path=db_path,
            emit=mutate_after_registration,
            scan_interval=0.2,
        )
        svc.start()
        time.sleep((SETTLE_DELAY_SECONDS * 2) + 2.0)
        svc.stop()

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT title, version, file_size FROM tracks").fetchall()
        conn.close()

        assert rows == [("Tagged", 1, wav.stat().st_size)]
        assert [e[0] for e in events] == ["new_track_detected"]
