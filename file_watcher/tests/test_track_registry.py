"""Tests for track registration, dedup, and versioning."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from file_watcher.track_registry import (
    RegistrationResult,
    compute_file_hash,
    ensure_schema,
    register_track,
)
from file_watcher.validator import AudioFormat


@pytest.fixture()
def db() -> sqlite3.Connection:
    """In-memory SQLite database with the tracks schema."""
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    # Also create the projects table stub so FK doesn't break.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT)"
    )
    conn.commit()
    return conn


def _write_audio(path: Path, content: bytes | None = None, size: int = 20_000) -> Path:
    """Write a fake audio file with a WAV-like header."""
    wav_header = b"RIFF\x00\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
    wav_header += b"\x01\x00\x02\x00\x44\xac\x00\x00\x10\xb1\x02\x00"
    wav_header += b"\x04\x00\x10\x00data\x00\x00\x00\x00"
    if content is None:
        padding = max(0, size - len(wav_header))
        content = wav_header + b"\x00" * padding
    path.write_bytes(content)
    return path


# ---------------------------------------------------------------------------
# Hash
# ---------------------------------------------------------------------------


class TestComputeFileHash:
    def test_deterministic(self, tmp_path: Path) -> None:
        f = _write_audio(tmp_path / "a.wav")
        h1 = compute_file_hash(f)
        h2 = compute_file_hash(f)
        assert h1 == h2

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.wav"
        f1.write_bytes(b"content A" * 100)
        f2 = tmp_path / "b.wav"
        f2.write_bytes(b"content B" * 100)
        assert compute_file_hash(f1) != compute_file_hash(f2)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegisterTrack:
    def test_new_track_created(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        f = _write_audio(tmp_path / "My Song.wav")
        result = register_track(db, f, fmt=AudioFormat.WAV, file_size=20_000)

        assert result.registered
        assert not result.duplicate
        assert result.track_id is not None
        assert result.track is not None
        assert result.track.title == "My Song"
        assert result.track.state == "DRAFT"
        assert result.track.version == 1
        assert result.track.parent_track_id is None

    def test_duplicate_hash_skipped(self, db: sqlite3.Connection, tmp_path: Path) -> None:
        f = _write_audio(tmp_path / "Song.wav")
        r1 = register_track(db, f, fmt=AudioFormat.WAV)
        r2 = register_track(db, f, fmt=AudioFormat.WAV)

        assert r1.registered
        assert not r2.registered
        assert r2.duplicate
        assert r2.track_id == r1.track_id

    def test_different_files_both_registered(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        f1 = _write_audio(tmp_path / "Song A.wav", content=b"aaa" * 5000)
        f2 = _write_audio(tmp_path / "Song B.wav", content=b"bbb" * 5000)
        r1 = register_track(db, f1, fmt=AudioFormat.WAV)
        r2 = register_track(db, f2, fmt=AudioFormat.WAV)

        assert r1.registered and r2.registered
        assert r1.track_id != r2.track_id


# ---------------------------------------------------------------------------
# Versioning / parent linking
# ---------------------------------------------------------------------------


class TestVersioning:
    def test_revision_links_to_parent(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        f1 = _write_audio(tmp_path / "My Song.wav", content=b"version1" * 2500)
        f2 = _write_audio(tmp_path / "My Song v2.wav", content=b"version2" * 2500)

        r1 = register_track(db, f1, fmt=AudioFormat.WAV)
        r2 = register_track(db, f2, fmt=AudioFormat.WAV)

        assert r1.registered and r2.registered
        assert r2.track is not None
        assert r2.track.parent_track_id == r1.track_id
        assert r2.track.version == 2

    def test_version_hint_respected(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        f1 = _write_audio(tmp_path / "Beat.wav", content=b"orig" * 2500)
        f2 = _write_audio(tmp_path / "Beat v5.wav", content=b"v5data" * 2500)

        r1 = register_track(db, f1, fmt=AudioFormat.WAV)
        r2 = register_track(db, f2, fmt=AudioFormat.WAV)

        assert r2.track is not None
        # Filename says v5, which is higher than the computed v2.
        assert r2.track.version == 5
        assert r2.track.parent_track_id == r1.track_id

    def test_third_revision_increments(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        f1 = _write_audio(tmp_path / "Groove.wav", content=b"g1" * 5000)
        f2 = _write_audio(tmp_path / "Groove v2.wav", content=b"g2" * 5000)
        f3 = _write_audio(tmp_path / "Groove v3.wav", content=b"g3" * 5000)

        r1 = register_track(db, f1, fmt=AudioFormat.WAV)
        r2 = register_track(db, f2, fmt=AudioFormat.WAV)
        r3 = register_track(db, f3, fmt=AudioFormat.WAV)

        assert r3.track is not None
        assert r3.track.version == 3
        # Parent should be the original track.
        assert r3.track.parent_track_id == r1.track_id

    def test_unrelated_titles_no_parent(
        self, db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        f1 = _write_audio(tmp_path / "Alpha.wav", content=b"alpha" * 2500)
        f2 = _write_audio(tmp_path / "Omega.wav", content=b"omega" * 2500)

        r1 = register_track(db, f1, fmt=AudioFormat.WAV)
        r2 = register_track(db, f2, fmt=AudioFormat.WAV)

        assert r2.track is not None
        assert r2.track.parent_track_id is None
        assert r2.track.version == 1


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestEnsureSchema:
    def test_idempotent(self) -> None:
        conn = sqlite3.connect(":memory:")
        ensure_schema(conn)
        ensure_schema(conn)  # should not raise
        # Verify table exists.
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tracks'"
        ).fetchone()
        assert row is not None
        conn.close()

    def test_index_created(self) -> None:
        conn = sqlite3.connect(":memory:")
        ensure_schema(conn)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_tracks_file_hash'"
        ).fetchone()
        assert row is not None
        conn.close()
