"""Detect whether a newly exported audio file differs from the previous export.

Strategy (cheap → expensive):
1. MD5 of file bytes — identical hash means identical file.
2. AcoustID/Chromaprint fingerprint comparison (if pyacoustid is available).
3. Fall back to "changed" with similarity ``None``.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ExportInfo:
    """Result of analyzing a single exported audio file."""

    file_path: str
    file_hash: str | None = None
    fingerprint: str | None = None
    changed_from_prev: bool | None = None
    similarity_score: float | None = None
    file_size: int | None = None
    duration_seconds: float | None = None
    sample_rate: int | None = None
    channels: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _md5(path: Path) -> str | None:
    try:
        h = hashlib.md5()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        logger.exception("Failed hashing %s", path)
        return None


def _read_audio_info(path: Path) -> tuple[float | None, int | None, int | None]:
    """Return (duration_seconds, sample_rate, channels) using mutagen."""
    try:
        from mutagen import File as MutagenFile  # type: ignore[import-untyped]
    except ImportError:
        return None, None, None
    try:
        mf = MutagenFile(str(path))
    except Exception:
        logger.exception("mutagen failed reading %s", path)
        return None, None, None
    if mf is None or getattr(mf, "info", None) is None:
        return None, None, None
    info = mf.info
    duration = getattr(info, "length", None)
    sr = getattr(info, "sample_rate", None)
    channels = getattr(info, "channels", None)
    return (
        float(duration) if duration is not None else None,
        int(sr) if sr is not None else None,
        int(channels) if channels is not None else None,
    )


def _fingerprint(path: Path) -> tuple[str | None, float | None]:
    """Return (chromaprint fingerprint, duration). Empty if unavailable."""
    try:
        import acoustid  # type: ignore[import-untyped]
    except ImportError:
        return None, None
    try:
        duration, fp_bytes = acoustid.fingerprint_file(str(path))
        fp = fp_bytes.decode("ascii") if isinstance(fp_bytes, bytes) else str(fp_bytes)
        return fp, float(duration)
    except Exception:
        logger.exception("Fingerprinting failed for %s", path)
        return None, None


def _fingerprint_similarity(a: str | None, b: str | None) -> float | None:
    """Crude Jaccard similarity over the fingerprint character n-grams.

    Real AcoustID uses chromaprint's compare API, which is unavailable
    in pure-Python. This bigram-Jaccard is good enough for "did this
    bounce change?" detection.
    """
    if not a or not b:
        return None
    if a == b:
        return 1.0

    def _ngrams(s: str, n: int = 6) -> set[str]:
        return {s[i : i + n] for i in range(0, max(0, len(s) - n + 1))}

    sa, sb = _ngrams(a), _ngrams(b)
    if not sa or not sb:
        return None
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_prev_export(project_name: str, db_path: str) -> ExportInfo | None:
    """Return the most recently inserted export for *project_name* (or None)."""
    try:
        conn = _connect(db_path)
    except sqlite3.Error:
        logger.exception("DB open failed")
        return None
    try:
        cur = conn.execute(
            """
            SELECT file_path, file_hash, fingerprint, changed_from_prev,
                   similarity_score, file_size, duration_seconds,
                   sample_rate, channels
            FROM export_events
            WHERE project_name = ?
            ORDER BY COALESCE(exported_at, created_at) DESC, id DESC
            LIMIT 1
            """,
            (project_name,),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return ExportInfo(
        file_path=row[0],
        file_hash=row[1],
        fingerprint=row[2],
        changed_from_prev=bool(row[3]) if row[3] is not None else None,
        similarity_score=row[4],
        file_size=row[5],
        duration_seconds=row[6],
        sample_rate=row[7],
        channels=row[8],
    )


def _project_name_from_path(file_path: Path) -> str:
    """Heuristic: parent folder name, stripped of any trailing ' Project'."""
    name = file_path.parent.name
    if name.endswith(" Project"):
        name = name[: -len(" Project")]
    return name or file_path.stem


def analyze_export(
    file_path: Path,
    db_path: str,
    project_name: str | None = None,
    session_id: int | None = None,
) -> ExportInfo:
    """Analyze and persist a new export.  Returns the populated ExportInfo."""
    file_path = Path(file_path)
    info = ExportInfo(file_path=str(file_path))

    try:
        info.file_size = file_path.stat().st_size
    except OSError:
        logger.exception("stat failed for %s", file_path)

    info.file_hash = _md5(file_path)

    proj = project_name or _project_name_from_path(file_path)
    prev = get_prev_export(proj, db_path)

    if prev is not None and info.file_hash and prev.file_hash == info.file_hash:
        info.changed_from_prev = False
        info.similarity_score = 1.0
        info.fingerprint = prev.fingerprint
    else:
        fp, _fp_duration = _fingerprint(file_path)
        info.fingerprint = fp
        if prev is not None and prev.fingerprint and fp:
            sim = _fingerprint_similarity(prev.fingerprint, fp)
            info.similarity_score = sim
            if sim is not None:
                info.changed_from_prev = sim < 0.99
            else:
                info.changed_from_prev = True
        else:
            info.changed_from_prev = prev is not None
            info.similarity_score = None

    duration, sr, channels = _read_audio_info(file_path)
    info.duration_seconds = duration
    info.sample_rate = sr
    info.channels = channels

    exported_at = datetime.now().isoformat(sep=" ", timespec="seconds")

    try:
        conn = _connect(db_path)
    except sqlite3.Error:
        logger.exception("DB open failed; export not persisted")
        return info
    try:
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO export_events (
                    session_id, project_name, file_path, file_hash, fingerprint,
                    changed_from_prev, similarity_score, file_size,
                    duration_seconds, sample_rate, channels, detected_bpm,
                    exported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    proj,
                    str(file_path),
                    info.file_hash,
                    info.fingerprint,
                    1 if info.changed_from_prev else 0 if info.changed_from_prev is False else None,
                    info.similarity_score,
                    info.file_size,
                    info.duration_seconds,
                    info.sample_rate,
                    info.channels,
                    None,
                    exported_at,
                ),
            )
    except sqlite3.Error:
        logger.exception("Failed to persist export %s", file_path)
    finally:
        conn.close()

    return info
