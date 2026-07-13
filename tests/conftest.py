"""
Shared fixtures for the AI Record Label integration test suite.

Run with:
    cd ~/gaer/ai-record-label
    .venv/bin/pytest tests/ -v
"""

from __future__ import annotations

import math
import sqlite3
import struct
import wave
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Migration helper
# ---------------------------------------------------------------------------

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "schema" / "migrations"


def _apply_migrations(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        conn.executescript(migration.read_text())
    conn.commit()


@pytest.fixture
def fresh_db(tmp_path: Path) -> str:
    """Fully migrated SQLite DB (all 6 migrations applied)."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    _apply_migrations(conn)
    conn.close()
    return str(db_path)


# ---------------------------------------------------------------------------
# Audio file factories
# ---------------------------------------------------------------------------

def _make_wav(path: Path, freq: float = 440.0, duration: float = 2.0) -> Path:
    """Write a minimal valid WAV (sine wave) to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 22050
    n = int(sample_rate * duration)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for i in range(n):
            val = int(32767 * math.sin(2 * math.pi * freq * i / sample_rate) * 0.5)
            wf.writeframes(struct.pack("<h", val))
    return path


@pytest.fixture
def wav_file(tmp_path: Path) -> Path:
    """A single valid WAV file."""
    return _make_wav(tmp_path / "test.wav")


@pytest.fixture
def make_wav(tmp_path: Path):
    """Factory: make a WAV with a given name and frequency."""
    def _factory(name: str = "out.wav", freq: float = 440.0, duration: float = 2.0) -> Path:
        return _make_wav(tmp_path / name, freq=freq, duration=duration)
    return _factory


@pytest.fixture
def project_wav(tmp_path: Path) -> Path:
    """WAV inside an Ableton-style project folder (for project name detection)."""
    return _make_wav(tmp_path / "Late Night Drive Project" / "late_night_drive_v3.wav")
