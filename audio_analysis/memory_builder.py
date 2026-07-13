"""Memory builder: detects patterns across tracks and maintains audio_memory."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .gemini_client import DEFAULT_OPENROUTER_MODEL, OPENROUTER_URL, _openrouter_key
from .models import (
    ArtistPatterns,
    AudioAnalysis,
    AudioMemoryEntry,
    MemoryCategory,
    TrackContext,
)

logger = logging.getLogger(__name__)

_MEMORY_PROMPT_PATH = Path(__file__).parent / "prompts" / "memory_prompt.txt"

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist (idempotent)."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS audio_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER NOT NULL,
            model_used TEXT NOT NULL DEFAULT 'gemini-3.1-pro-preview',
            bpm REAL, musical_key TEXT, energy_curve TEXT, structure TEXT,
            instruments TEXT, genre_tags TEXT, mood_tags TEXT,
            mix_observations TEXT, notable_moments TEXT, raw_response TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS audio_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            observation TEXT NOT NULL,
            confidence REAL DEFAULT 0.5,
            first_noticed_track_id INTEGER,
            supporting_track_ids TEXT,
            times_observed INTEGER DEFAULT 1,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


def _store_analysis(conn: sqlite3.Connection, track_id: int, analysis: AudioAnalysis) -> int:
    """Insert an AudioAnalysis row and return its ID."""
    cursor = conn.execute(
        """
        INSERT INTO audio_analyses
            (track_id, model_used, bpm, musical_key, energy_curve, structure,
             instruments, genre_tags, mood_tags, mix_observations,
             notable_moments, raw_response)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            track_id,
            analysis.model_used,
            analysis.bpm,
            analysis.musical_key,
            json.dumps([p.model_dump() for p in analysis.energy_curve]),
            json.dumps(analysis.structure),
            json.dumps(analysis.instruments),
            json.dumps(analysis.genre_tags),
            json.dumps(analysis.mood_tags),
            json.dumps([o.model_dump() for o in analysis.mix_observations]),
            json.dumps([m.model_dump() for m in analysis.notable_moments]),
            analysis.raw_response,
        ),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore[return-value]


def _load_all_analyses(conn: sqlite3.Connection, limit: int = 15) -> list[dict]:
    """Load recent past analyses as dicts (for the memory prompt).

    Capped at `limit` most recent rows — older patterns are already distilled
    into audio_memory and don't need to be re-sent to Gemini as raw JSON.
    """
    rows = conn.execute(
        "SELECT * FROM audio_analyses ORDER BY created_at DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    results: list[dict] = []
    for row in rows:
        d = dict(row)
        # raw_response can be multi-KB — drop it, pattern data is all we need
        d.pop("raw_response", None)
        for json_field in (
            "energy_curve", "structure", "instruments", "genre_tags",
            "mood_tags", "mix_observations", "notable_moments",
        ):
            if d.get(json_field):
                try:
                    d[json_field] = json.loads(d[json_field])
                except (json.JSONDecodeError, TypeError):
                    pass
        results.append(d)
    # Return chronological order so the prompt reads oldest→newest
    return list(reversed(results))


def _load_memory_entries(conn: sqlite3.Connection) -> list[AudioMemoryEntry]:
    """Load all existing audio_memory entries."""
    rows = conn.execute(
        "SELECT * FROM audio_memory ORDER BY confidence DESC"
    ).fetchall()
    entries: list[AudioMemoryEntry] = []
    for row in rows:
        d = dict(row)
        supporting = d.get("supporting_track_ids")
        if supporting:
            try:
                supporting = json.loads(supporting)
            except (json.JSONDecodeError, TypeError):
                supporting = []
        else:
            supporting = []
        entries.append(
            AudioMemoryEntry(
                id=d["id"],
                category=MemoryCategory(d["category"]),
                observation=d["observation"],
                confidence=d.get("confidence", 0.5),
                first_noticed_track_id=d.get("first_noticed_track_id"),
                supporting_track_ids=supporting,
                times_observed=d.get("times_observed", 1),
                last_updated=d.get("last_updated"),
                created_at=d.get("created_at"),
            )
        )
    return entries


def _load_memory_prompt() -> str:
    return _MEMORY_PROMPT_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Pattern detection via OpenRouter
# ---------------------------------------------------------------------------


async def _detect_patterns(
    new_analysis: dict,
    previous_analyses: list[dict],
    existing_memory: list[AudioMemoryEntry],
    *,
    model: str = DEFAULT_OPENROUTER_MODEL,
    api_key: str | None = None,
) -> dict:
    """Call Gemini through OpenRouter to detect patterns across the catalog.

    Returns the raw parsed JSON dict from the memory prompt.
    """
    prompt_template = _load_memory_prompt()

    memory_text = json.dumps(
        [e.model_dump(mode="json") for e in existing_memory], indent=2
    )
    prev_text = json.dumps(previous_analyses, indent=2, default=str)
    new_text = json.dumps(new_analysis, indent=2, default=str)

    prompt = (
        prompt_template
        .replace("{existing_memory}", memory_text)
        .replace("{previous_analyses}", prev_text)
        .replace("{new_analysis}", new_text)
    )

    key = _openrouter_key(api_key or os.environ.get("OPENROUTER_API_KEY"))
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 8192,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://ai-record-label.local",
        "X-Title": "AI Record Label",
    }
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(OPENROUTER_URL, headers=headers, json=payload)
        response.raise_for_status()
    body = response.json()
    raw = body["choices"][0]["message"]["content"] or ""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    return json.loads(text)


def _apply_memory_updates(
    conn: sqlite3.Connection,
    track_id: int,
    pattern_data: dict,
) -> None:
    """Write pattern detection results back to the audio_memory table."""
    now = datetime.now(timezone.utc).isoformat()

    for entry in pattern_data.get("updated_entries", []):
        existing_id = entry.get("id")
        category = entry["category"]
        observation = entry["observation"]
        confidence = entry.get("confidence", 0.5)

        if existing_id:
            # Update existing entry
            row = conn.execute(
                "SELECT supporting_track_ids, times_observed FROM audio_memory WHERE id = ?",
                (existing_id,),
            ).fetchone()
            if row:
                existing_supporting = json.loads(row["supporting_track_ids"] or "[]")
                if track_id not in existing_supporting:
                    existing_supporting.append(track_id)
                conn.execute(
                    """
                    UPDATE audio_memory
                    SET confidence = ?, observation = ?,
                        supporting_track_ids = ?, times_observed = ?,
                        last_updated = ?
                    WHERE id = ?
                    """,
                    (
                        confidence,
                        observation,
                        json.dumps(existing_supporting),
                        row["times_observed"] + 1,
                        now,
                        existing_id,
                    ),
                )
        else:
            # Insert new entry
            conn.execute(
                """
                INSERT INTO audio_memory
                    (category, observation, confidence, first_noticed_track_id,
                     supporting_track_ids, times_observed, last_updated, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    category,
                    observation,
                    confidence,
                    track_id,
                    json.dumps([track_id]),
                    now,
                    now,
                ),
            )

    conn.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def build_memory(
    db_path: str,
    track_id: int,
    analysis: AudioAnalysis,
    *,
    model: str = DEFAULT_OPENROUTER_MODEL,
    api_key: str | None = None,
) -> TrackContext:
    """Run the memory-building pipeline after a new analysis.

    1. Stores the analysis in the DB.
    2. Loads all past analyses and existing memory.
    3. Calls Gemini for pattern detection.
    4. Updates the audio_memory table.
    5. Returns TrackContext for the analyzed track.
    """
    conn = _get_connection(db_path)
    _ensure_tables(conn)

    _store_analysis(conn, track_id, analysis)

    all_analyses = _load_all_analyses(conn)
    existing_memory = _load_memory_entries(conn)

    # The new analysis as a simple dict for the prompt (raw_response already stripped)
    new_analysis_dict = all_analyses[-1] if all_analyses else {}

    # Recent analyses (already capped + raw_response stripped in _load_all_analyses)
    previous = all_analyses[:-1] if len(all_analyses) > 1 else []

    try:
        pattern_data = await _detect_patterns(
            new_analysis=new_analysis_dict,
            previous_analyses=previous,
            existing_memory=existing_memory,
            model=model,
            api_key=api_key,
        )
    except Exception:
        logger.exception("Pattern detection failed; skipping memory update")
        conn.close()
        return TrackContext(track_id=track_id)

    _apply_memory_updates(conn, track_id, pattern_data)
    conn.close()

    # Build TrackContext from the response
    ctx = pattern_data.get("track_context", {})
    return TrackContext(
        track_id=track_id,
        similarities_to_past=ctx.get("similarities_to_past", []),
        departures_from_past=ctx.get("departures_from_past", []),
        evolution_notes=ctx.get("evolution_notes", []),
        confirmed_patterns=ctx.get("confirmed_patterns", []),
        new_observations=ctx.get("new_observations", []),
    )


def store_analysis_sync(db_path: str, track_id: int, analysis: AudioAnalysis) -> int:
    """Synchronous helper to store an analysis without memory building."""
    conn = _get_connection(db_path)
    _ensure_tables(conn)
    row_id = _store_analysis(conn, track_id, analysis)
    conn.close()
    return row_id


async def refresh_catalog_memory(
    db_path: str,
    anchor_track_id: int,
    *,
    model: str = DEFAULT_OPENROUTER_MODEL,
    api_key: str | None = None,
) -> int:
    """Re-run cross-track pattern detection without re-storing any analysis.

    Used after a multi-track album drop so patterns across the full catalog are
    detected in a single Gemini pass rather than track-by-track in isolation.

    Args:
        db_path: Path to the SQLite database.
        anchor_track_id: The track to treat as "newest" in the prompt.  Usually
            the last track added to the project.
        model: Model to use for pattern detection.
        api_key: Optional OpenRouter API key override.

    Returns:
        Number of memory entries created or updated.

    Raises:
        RuntimeError: If there are fewer than 2 analyses in the DB (nothing to
            cross-reference — raise rather than silently skip).
    """
    conn = _get_connection(db_path)
    _ensure_tables(conn)

    all_analyses = _load_all_analyses(conn)
    if len(all_analyses) < 2:
        conn.close()
        raise RuntimeError(
            f"refresh_catalog_memory requires at least 2 analyses in the DB; "
            f"found {len(all_analyses)}"
        )

    existing_memory = _load_memory_entries(conn)

    # Use the stored analysis for anchor_track_id as the "new" focal point.
    anchor = next(
        (a for a in reversed(all_analyses) if a.get("track_id") == anchor_track_id),
        all_analyses[-1],
    )
    previous = [a for a in all_analyses if a is not anchor]

    pattern_data = await _detect_patterns(
        new_analysis=anchor,
        previous_analyses=previous,
        existing_memory=existing_memory,
        model=model,
        api_key=api_key,
    )

    _apply_memory_updates(conn, anchor_track_id, pattern_data)
    conn.close()

    return len(pattern_data.get("updated_entries", []))


# ---------------------------------------------------------------------------
# Query interface
# ---------------------------------------------------------------------------


def get_artist_patterns(
    db_path: str,
    min_confidence: float = 0.4,
    limit: int = 30,
) -> ArtistPatterns:
    """Return audio memory entries grouped by type, filtered by confidence, sorted DESC."""
    conn = _get_connection(db_path)
    _ensure_tables(conn)
    rows = conn.execute(
        "SELECT * FROM audio_memory WHERE confidence >= ? ORDER BY confidence DESC LIMIT ?",
        (min_confidence, limit),
    ).fetchall()
    conn.close()

    entries: list[AudioMemoryEntry] = []
    for row in rows:
        d = dict(row)
        supporting = d.get("supporting_track_ids")
        try:
            supporting = json.loads(supporting) if supporting else []
        except (json.JSONDecodeError, TypeError):
            supporting = []
        entries.append(
            AudioMemoryEntry(
                id=d["id"],
                category=MemoryCategory(d["category"]),
                observation=d["observation"],
                confidence=d.get("confidence", 0.5),
                first_noticed_track_id=d.get("first_noticed_track_id"),
                supporting_track_ids=supporting,
                times_observed=d.get("times_observed", 1),
                last_updated=d.get("last_updated"),
                created_at=d.get("created_at"),
            )
        )

    patterns = ArtistPatterns(all_entries=entries)

    category_map: dict[MemoryCategory, str] = {
        MemoryCategory.SIGNATURE_SOUND: "signature_sounds",
        MemoryCategory.RECURRING_STRENGTH: "strengths",
        MemoryCategory.RECURRING_WEAKNESS: "weaknesses",
        MemoryCategory.PRODUCTION_PATTERN: "production_patterns",
        MemoryCategory.GENRE_TENDENCY: "genre_tendencies",
        MemoryCategory.EVOLUTION_NOTE: "evolution_notes",
    }

    for entry in entries:
        field = category_map.get(entry.category)
        if field:
            getattr(patterns, field).append(entry)

    return patterns


def get_track_context(db_path: str, track_id: int) -> TrackContext:
    """Return how a specific track relates to the artist's catalog."""
    conn = _get_connection(db_path)
    _ensure_tables(conn)
    entries = _load_memory_entries(conn)
    conn.close()

    ctx = TrackContext(track_id=track_id)
    for entry in entries:
        if entry.first_noticed_track_id == track_id:
            ctx.new_observations.append(entry.observation)
        if track_id in entry.supporting_track_ids:
            ctx.confirmed_patterns.append(entry.observation)
        if entry.category == MemoryCategory.EVOLUTION_NOTE:
            ctx.evolution_notes.append(entry.observation)

    return ctx


def get_evolution_arc(db_path: str) -> list[AudioMemoryEntry]:
    """Return evolution_note entries in chronological order."""
    conn = _get_connection(db_path)
    _ensure_tables(conn)
    rows = conn.execute(
        "SELECT * FROM audio_memory WHERE category = ? ORDER BY created_at ASC",
        (MemoryCategory.EVOLUTION_NOTE.value,),
    ).fetchall()
    conn.close()

    results: list[AudioMemoryEntry] = []
    for row in rows:
        d = dict(row)
        supporting = d.get("supporting_track_ids")
        try:
            supporting = json.loads(supporting) if supporting else []
        except (json.JSONDecodeError, TypeError):
            supporting = []
        results.append(
            AudioMemoryEntry(
                id=d["id"],
                category=MemoryCategory(d["category"]),
                observation=d["observation"],
                confidence=d.get("confidence", 0.5),
                first_noticed_track_id=d.get("first_noticed_track_id"),
                supporting_track_ids=supporting,
                times_observed=d.get("times_observed", 1),
                last_updated=d.get("last_updated"),
                created_at=d.get("created_at"),
            )
        )
    return results


def get_strengths_and_weaknesses(db_path: str) -> dict[str, list[AudioMemoryEntry]]:
    """Return recurring strengths and weaknesses."""
    conn = _get_connection(db_path)
    _ensure_tables(conn)
    entries = _load_memory_entries(conn)
    conn.close()

    return {
        "strengths": [
            e for e in entries if e.category == MemoryCategory.RECURRING_STRENGTH
        ],
        "weaknesses": [
            e for e in entries if e.category == MemoryCategory.RECURRING_WEAKNESS
        ],
    }
