"""Track registration in SQLite -- dedup, versioning, and parent linking."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field
from thefuzz import fuzz

from file_watcher.naming_parser import ParsedFilename, parse_filename
from file_watcher.validator import AudioFormat

logger = logging.getLogger(__name__)

# Fuzzy-match threshold for linking a new file to an existing track title.
_FUZZY_MATCH_THRESHOLD = 75

# Buffer size for SHA-256 hashing (128 KB).
_HASH_BUFFER_SIZE = 128 * 1024


class TrackRecord(BaseModel):
    """A track row as returned from the database."""

    id: int
    title: str | None = None
    file_path: str
    file_hash: str
    file_size: int | None = None
    duration_seconds: float | None = None
    format: str | None = None
    parent_track_id: int | None = None
    version: int = 1
    state: str = "DRAFT"
    project_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RegistrationResult(BaseModel):
    """Outcome of attempting to register a track."""

    registered: bool = Field(description="True if a new record was created")
    duplicate: bool = Field(default=False, description="True if file hash already existed")
    track_id: int | None = Field(default=None, description="ID of new or existing track")
    track: TrackRecord | None = None


# ---------------------------------------------------------------------------
# Schema -- called once at startup to ensure the table exists.
# ---------------------------------------------------------------------------

_CREATE_TRACKS_TABLE = """\
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    file_size INTEGER,
    duration_seconds REAL,
    format TEXT,
    parent_track_id INTEGER REFERENCES tracks(id),
    version INTEGER DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'DRAFT',
    project_id INTEGER REFERENCES projects(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_CREATE_HASH_INDEX = """\
CREATE INDEX IF NOT EXISTS idx_tracks_file_hash ON tracks(file_hash);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the tracks table and indices if they don't exist."""
    conn.execute(_CREATE_TRACKS_TABLE)
    conn.execute(_CREATE_HASH_INDEX)
    conn.commit()


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def compute_file_hash(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    sha = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(_HASH_BUFFER_SIZE)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


# ---------------------------------------------------------------------------
# Duration via mutagen
# ---------------------------------------------------------------------------


def _get_duration(path: Path) -> float | None:
    """Get audio duration in seconds using mutagen.

    Returns None if mutagen cannot parse the file.
    """
    try:
        import mutagen  # noqa: PLC0415

        audio = mutagen.File(str(path))  # type: ignore[attr-defined]
        if audio is not None and audio.info is not None:
            return float(audio.info.length)
    except Exception:
        logger.debug("mutagen could not determine duration for %s", path.name, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# Fuzzy matching against existing tracks
# ---------------------------------------------------------------------------


def _find_parent_track(
    conn: sqlite3.Connection,
    parsed: ParsedFilename,
) -> tuple[int | None, int]:
    """Find a parent track by fuzzy-matching the parsed title.

    Returns:
        (parent_track_id, next_version) -- parent is None if no match found.
    """
    cursor = conn.execute(
        "SELECT id, title, version FROM tracks WHERE title IS NOT NULL ORDER BY id",
    )
    rows = cursor.fetchall()

    best_id: int | None = None
    best_score = 0
    best_version = 0

    for row_id, row_title, row_version in rows:
        score = fuzz.token_sort_ratio(parsed.title.lower(), row_title.lower())
        if score >= _FUZZY_MATCH_THRESHOLD and score > best_score:
            best_score = score
            best_id = row_id
            best_version = row_version

    if best_id is not None:
        # Walk the chain to find the latest version number for this lineage.
        max_version_row = conn.execute(
            """\
            SELECT MAX(version) FROM tracks
            WHERE id = ? OR parent_track_id = ?
            """,
            (best_id, best_id),
        ).fetchone()
        max_version = max_version_row[0] if max_version_row and max_version_row[0] else best_version
        return best_id, max_version + 1

    return None, 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def register_track(
    conn: sqlite3.Connection,
    file_path: str | Path,
    fmt: AudioFormat | None = None,
    file_size: int | None = None,
) -> RegistrationResult:
    """Register a new audio file as a track in the database.

    Computes the file hash, checks for exact duplicates, parses the
    filename for metadata, links revisions to parent tracks, and inserts
    a new row with state='DRAFT'.

    Args:
        conn: Open SQLite connection.
        file_path: Path to the validated audio file.
        fmt: Audio format (from validator). Re-detected if not provided.
        file_size: File size in bytes. Read from disk if not provided.

    Returns:
        A ``RegistrationResult`` describing what happened.
    """
    path = Path(file_path)

    # --- Hash & dedup ---
    file_hash = compute_file_hash(path)

    existing = conn.execute(
        "SELECT id FROM tracks WHERE file_hash = ?", (file_hash,)
    ).fetchone()
    if existing is not None:
        logger.debug("Duplicate file hash %s -- skipping %s", file_hash[:12], path.name)
        return RegistrationResult(
            registered=False,
            duplicate=True,
            track_id=existing[0],
        )

    # --- Metadata ---
    parsed = parse_filename(path)
    if file_size is None:
        file_size = path.stat().st_size
    duration = _get_duration(path)

    # --- Parent linking ---
    parent_id, version = _find_parent_track(conn, parsed)

    # If the filename had an explicit version hint, prefer it when it's
    # higher than our computed version (the artist labelled it "v5").
    if parsed.version_hint is not None and parsed.version_hint > version:
        version = parsed.version_hint

    # --- Insert ---
    now = datetime.now(tz=timezone.utc).isoformat()
    cursor = conn.execute(
        """\
        INSERT INTO tracks (
            title, file_path, file_hash, file_size, duration_seconds,
            format, parent_track_id, version, state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?)
        """,
        (
            parsed.title,
            str(path),
            file_hash,
            file_size,
            duration,
            fmt.value if fmt else None,
            parent_id,
            version,
            now,
            now,
        ),
    )
    conn.commit()
    track_id = cursor.lastrowid
    assert track_id is not None

    logger.info(
        "Registered track #%d '%s' (v%d, parent=%s)",
        track_id,
        parsed.title,
        version,
        parent_id,
    )

    track = TrackRecord(
        id=track_id,
        title=parsed.title,
        file_path=str(path),
        file_hash=file_hash,
        file_size=file_size,
        duration_seconds=duration,
        format=fmt.value if fmt else None,
        parent_track_id=parent_id,
        version=version,
        state="DRAFT",
        created_at=datetime.fromisoformat(now),
        updated_at=datetime.fromisoformat(now),
    )

    return RegistrationResult(
        registered=True,
        duplicate=False,
        track_id=track_id,
        track=track,
    )
