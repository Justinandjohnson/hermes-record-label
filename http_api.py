"""HTTP API server for AI Record Label — exposes SQLite DB over HTTP on port 8086.

Designed for remote access from Windows machines and the desktop app via a
Cloudflare tunnel.  Uses only stdlib + packages already in pyproject.toml.
"""

from __future__ import annotations

import collections
import hashlib
import hmac
import ipaddress
import json
import logging
import mimetypes
import os
import platform
import secrets
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("http_api")

# ── TEMP DIAGNOSTIC: log every sqlite3.connect() call with a stack trace ──
if os.environ.get("ARL_TRACE_LOCKS"):
    import traceback as _tb

    _orig_sqlite_connect = sqlite3.connect
    _trace_lock = threading.Lock()
    _connect_log: list[dict] = []
    _conn_counter = [0]

    def _traced_connect(*args, **kwargs):
        conn = _orig_sqlite_connect(*args, **kwargs)
        with _trace_lock:
            _conn_counter[0] += 1
            cid = _conn_counter[0]
        stack = "".join(_tb.format_stack(limit=10)[:-1])
        entry = {
            "id": cid,
            "opened_at": time.time(),
            "stack": stack,
            "thread": threading.current_thread().name,
        }
        with _trace_lock:
            _connect_log.append(entry)
            # Keep it bounded — this is a short diagnostic run.
            if len(_connect_log) > 500:
                del _connect_log[:250]
        return conn

    sqlite3.connect = _traced_connect

    def _lock_watchdog() -> None:
        # Every 3s, dump every connect() call made in the trailing 90s window —
        # a rough proxy for "still plausibly open", since sqlite3.Connection
        # can't be monkey-patched (C extension) or weakref'd to detect close().
        while True:
            time.sleep(3.0)
            now = time.time()
            with _trace_lock:
                recent = [e for e in _connect_log if now - e["opened_at"] < 90]
            if len(recent) >= 3:
                logger.error("LOCK TRACE: %d sqlite3.connect() calls in last 90s:", len(recent))
                for e in recent:
                    age = now - e["opened_at"]
                    logger.error(
                        "  conn#%d age=%.1fs thread=%s\n%s", e["id"], age, e["thread"], e["stack"]
                    )

    threading.Thread(target=_lock_watchdog, daemon=True, name="lock-watchdog").start()

# ── Data-dir / DB path (mirrors mcp_server.py) ────────────────────────────────


def _resolve_data_dir() -> Path:
    explicit = os.environ.get("AI_RECORD_LABEL_DATA")
    if explicit:
        return Path(explicit)
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "ai-record-label"
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(appdata) / "ai-record-label"
    else:
        return Path.home() / ".local" / "share" / "ai-record-label"


_DATA_DIR: Path = _resolve_data_dir()
_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH: str = os.environ.get("DB_PATH", str(_DATA_DIR / "hermes.db"))

# ── Static file serving (Vite build output) ──────────────────────────────────

STATIC_DIR: Path = Path(__file__).resolve().parent / "desktop-app" / "dist"

# ── Bearer-token management ────────────────────────────────────────────────────

_TOKEN_FILE: Path = _DATA_DIR / "api_token.txt"
_token_lock = threading.Lock()
_cached_token: str | None = None


def _load_or_create_token() -> str:
    """Return the API bearer token.

    Priority:
    1. BACKEND_API_TOKEN env var — set this in Render's environment tab so you
       always know the token without digging through logs.
    2. Existing token file — persists across restarts on Mac and on Render's
       persistent disk (/data/api_token.txt).
    3. Auto-generate a new token and save it to the token file.
    """
    global _cached_token
    with _token_lock:
        if _cached_token is not None:
            return _cached_token
        # 1. Env var takes precedence (useful for Render / Docker deployments)
        env_tok = os.environ.get("BACKEND_API_TOKEN", "").strip()
        if env_tok:
            _cached_token = env_tok
            return _cached_token
        # 2. Persisted token file
        if _TOKEN_FILE.exists():
            tok = _TOKEN_FILE.read_text().strip()
            if tok:
                _cached_token = tok
                return _cached_token
        # 3. Generate and save a new token
        tok = secrets.token_hex(32)
        _TOKEN_FILE.write_text(tok)
        _cached_token = tok
        return _cached_token


# ── Database helpers ───────────────────────────────────────────────────────────


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _db_conn() -> sqlite3.Connection:
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


# ── Query helpers ──────────────────────────────────────────────────────────────


def _get_tracks() -> list[dict]:
    with _db_conn() as conn:
        rows = conn.execute(
            """SELECT id, title, file_path, file_hash, file_size, duration_seconds,
                      format, parent_track_id, version, state, project_id,
                      created_at, updated_at
               FROM tracks ORDER BY created_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def _get_stats() -> dict:
    with _db_conn() as conn:
        in_progress: int = conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE state NOT IN ('RELEASED', 'VAULT')"
        ).fetchone()[0]
        released: int = conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE state = 'RELEASED'"
        ).fetchone()[0]
        total = in_progress + released
        completion_rate = round((released / total * 100) if total > 0 else 0.0, 1)

        # Reputation from artist_stats
        reputation = 0
        if _table_exists(conn, "artist_stats"):
            row = conn.execute(
                """SELECT CAST(value AS INTEGER)
                   FROM artist_stats
                   WHERE stat_type = 'reputation'
                   ORDER BY created_at DESC LIMIT 1"""
            ).fetchone()
            if row:
                reputation = row[0] or 0

        # Streaks from creation_streaks
        current_streak = 0
        longest_streak = 0
        if _table_exists(conn, "creation_streaks"):
            # current streak: the most recent open streak (ended_at IS NULL)
            row = conn.execute(
                """SELECT length_days FROM creation_streaks
                   WHERE ended_at IS NULL
                   ORDER BY started_at DESC LIMIT 1"""
            ).fetchone()
            if row and row[0] is not None:
                current_streak = int(row[0])

            # longest streak ever
            row = conn.execute("SELECT MAX(length_days) FROM creation_streaks").fetchone()
            if row and row[0] is not None:
                longest_streak = int(row[0])

    return {
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "reputation": reputation,
        "tracks_in_progress": in_progress,
        "tracks_released": released,
        "completion_rate": completion_rate,
    }


def _get_feedback(track_id: int) -> list[dict]:
    with _db_conn() as conn:
        rows = conn.execute(
            """SELECT id, track_id, project_id, agent, message, channel,
                      direction, intent, timestamp_sec, created_at
               FROM feedback WHERE track_id = ? ORDER BY created_at ASC""",
            (track_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _get_analysis(track_id: int) -> dict[str, Any] | None:
    with _db_conn() as conn:
        if not _table_exists(conn, "audio_analyses"):
            return None
        row = conn.execute(
            """SELECT bpm, musical_key, genre_tags, mood_tags, energy_curve,
                      structure, instruments, mix_observations, notable_moments, model_used
               FROM audio_analyses WHERE track_id = ? ORDER BY id DESC LIMIT 1""",
            (track_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        for field in ("genre_tags", "mood_tags", "instruments", "mix_observations", "notable_moments"):
            if isinstance(data.get(field), str):
                try:
                    data[field] = json.loads(data[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return data


def _parse_multipart_parts(content_type: str, body: bytes) -> list[dict[str, Any]]:
    """Parse a multipart/form-data body into parts: {name, filename, content_type, data}."""
    import email

    raw_msg = b"MIME-Version: 1.0\r\nContent-Type: " + content_type.encode() + b"\r\n\r\n" + body
    msg = email.message_from_bytes(raw_msg)

    parts: list[dict[str, Any]] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        cd = part.get("Content-Disposition", "")
        if not cd:
            continue
        field_name = ""
        filename = ""
        for seg in cd.split(";"):
            seg = seg.strip()
            if seg.lower().startswith("name="):
                field_name = seg[5:].strip().strip('"')
            elif seg.lower().startswith("filename="):
                filename = seg[9:].strip().strip('"')
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        parts.append(
            {
                "name": field_name,
                "filename": filename,
                "content_type": part.get_content_type(),
                "data": payload,
            }
        )
    return parts


def _get_feedback_by_id(message_id: int) -> dict | None:
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT id, agent, message FROM feedback WHERE id = ?",
            (message_id,),
        ).fetchone()
    return dict(row) if row else None


def _get_track_audio_path(track_id: int) -> Path | None:
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT file_path FROM tracks WHERE id = ?",
            (track_id,),
        ).fetchone()
    if not row or not row["file_path"]:
        return None
    return Path(row["file_path"]).expanduser()


def _get_sessions(limit: int = 20) -> list[dict]:
    with _db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM ableton_sessions ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def _get_export_events(limit: int = 20) -> list[dict]:
    with _db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM export_events ORDER BY exported_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def _get_artist_profile() -> dict | None:
    with _db_conn() as conn:
        row = conn.execute(
            """SELECT id, name, genre, subgenres, influences, sound_description,
                      bandcamp_url, quiet_hours_start, quiet_hours_end, quiet_days,
                      timezone, onboarded_at
               FROM artist_profile LIMIT 1"""
        ).fetchone()
    return dict(row) if row else None


def _get_projects() -> list[dict]:
    with _db_conn() as conn:
        rows = conn.execute(
            """SELECT id, title, type, state, target_track_count,
                      target_release_date, created_at
               FROM projects ORDER BY created_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def _get_release_states(track_id: int) -> list[dict]:
    with _db_conn() as conn:
        rows = conn.execute(
            """SELECT id, track_id, from_state, to_state, changed_by,
                      reason, bandcamp_job_id, created_at
               FROM release_states WHERE track_id = ? ORDER BY created_at ASC""",
            (track_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Roundtable verdicts ────────────────────────────────────────────────────


def _get_active_verdict(track_id: int) -> dict | None:
    """Active (non-superseded) verdict for a track, or None.

    Returns the row as a dict with next_action_payload decoded from JSON.
    """
    with _db_conn() as conn:
        row = conn.execute(
            """SELECT id, track_id, recommendation, headline, reasoning,
                      next_action_kind, next_action_payload,
                      created_at, superseded_at
               FROM roundtable_verdicts
               WHERE track_id = ? AND superseded_at IS NULL""",
            (track_id,),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    raw_payload = result.get("next_action_payload")
    if raw_payload:
        try:
            result["next_action_payload"] = json.loads(raw_payload)
        except (TypeError, json.JSONDecodeError):
            result["next_action_payload"] = None
    else:
        result["next_action_payload"] = None
    return result


def _synthesize_verdict(track_id: int) -> dict:
    """Call the verdict synthesizer and return the stored row.

    Imported lazily so httpx + the verdict module don't slow server startup.
    Runs the async synthesizer via asyncio.run within the handler thread.
    """
    import asyncio

    from coordination.verdict_synthesizer import (
        VerdictSynthesisError,
        synthesize_verdict,
    )

    try:
        return asyncio.run(synthesize_verdict(DB_PATH, track_id))
    except VerdictSynthesisError as exc:
        raise ValueError(str(exc)) from exc


def _act_on_verdict(track_id: int, verdict_id: int) -> dict:
    """Execute the next_action attached to a verdict.

    Returns {"track": <updated track>, "wave_vault_added": <int>} when relevant.
    """
    with _db_conn() as conn:
        row = conn.execute(
            """SELECT recommendation, next_action_kind, next_action_payload, headline,
                      superseded_at
               FROM roundtable_verdicts WHERE id = ? AND track_id = ?""",
            (verdict_id, track_id),
        ).fetchone()
    if row is None:
        raise ValueError(f"Verdict {verdict_id} not found for track {track_id}")
    if row["superseded_at"] is not None:
        raise ValueError("Verdict has been superseded — fetch the current one")

    kind = row["next_action_kind"]
    try:
        payload = json.loads(row["next_action_payload"]) if row["next_action_payload"] else {}
    except (TypeError, json.JSONDecodeError):
        payload = {}
    headline = row["headline"] or "Roundtable verdict"

    if kind == "approve":
        # FEEDBACK_GIVEN → APPROVED → ART_NEEDED. Walk through both transitions
        # so the state machine stays consistent.
        track = _transition_track_state(
            track_id=track_id,
            to_state="APPROVED",
            changed_by="roundtable",
            reason=headline,
        )
        track = _transition_track_state(
            track_id=track_id,
            to_state="ART_NEEDED",
            changed_by="roundtable",
            reason="ready for Maren",
        )
        return {"track": track, "wave_vault_added": 0}

    if kind == "request_revision":
        focus_areas = payload.get("focus_areas") or []
        reason = "; ".join(focus_areas) if focus_areas else headline
        # Already in FEEDBACK_GIVEN when verdict lands; only transition if not.
        with _db_conn() as conn:
            cur = conn.execute("SELECT state FROM tracks WHERE id = ?", (track_id,)).fetchone()
            if cur and str(cur["state"]).upper() == "FEEDBACK_GIVEN":
                track = _track_payload(conn, track_id)
            else:
                conn.close()
                track = _transition_track_state(
                    track_id=track_id,
                    to_state="FEEDBACK_GIVEN",
                    changed_by="roundtable",
                    reason=reason,
                )
        return {"track": track, "wave_vault_added": 0}

    if kind == "vault":
        track = _vault_track(track_id=track_id, reason=headline)
        return {"track": track, "wave_vault_added": 0}

    if kind == "wave_vault":
        segments = payload.get("segments") or []
        added = 0
        with _db_conn() as conn:
            for seg in segments:
                stem = seg.get("stem")
                if stem not in {"vocals", "drums", "bass", "other", "full"}:
                    continue
                conn.execute(
                    """INSERT INTO wave_vault
                          (track_id, stem, start_sec, end_sec, notes,
                           added_by, added_at)
                       VALUES (?, ?, ?, ?, ?, 'roundtable', datetime('now'))""",
                    (
                        track_id,
                        stem,
                        seg.get("start_sec"),
                        seg.get("end_sec"),
                        seg.get("notes"),
                    ),
                )
                added += 1
            conn.commit()
        # After saving loops, vault the track itself
        track = _vault_track(track_id=track_id, reason=f"{headline} (loops saved)")
        return {"track": track, "wave_vault_added": added}

    raise ValueError(f"Unknown next_action_kind: {kind!r}")


_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"IN_REVIEW"},
    "IN_REVIEW": {"FEEDBACK_GIVEN"},
    "FEEDBACK_GIVEN": {"DRAFT", "APPROVED"},
    "APPROVED": {"ART_NEEDED"},
    "ART_NEEDED": {"ART_SUBMITTED"},
    "ART_SUBMITTED": {"ART_NEEDED", "ART_APPROVED"},
    "ART_APPROVED": {"RELEASE_READY"},
    "RELEASE_READY": {"PREFLIGHT"},
    "PREFLIGHT": {"UPLOADING", "ART_NEEDED", "FEEDBACK_GIVEN"},
    "UPLOADING": {"RELEASED", "RELEASE_READY"},
    "RELEASED": set(),
}


def _track_payload(conn: sqlite3.Connection, track_id: int) -> dict:
    row = conn.execute(
        """SELECT id, title, file_path, file_hash, file_size,
                  duration_seconds, format, parent_track_id, version,
                  state, project_id, created_at, updated_at
           FROM tracks WHERE id = ?""",
        (track_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Track {track_id} not found")
    return dict(row)


def _transition_track_state(
    *,
    track_id: int,
    to_state: str,
    changed_by: str = "artist",
    reason: str | None = None,
) -> dict:
    to_state = to_state.upper()
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT id, state FROM tracks WHERE id = ?",
            (track_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Track {track_id} not found")

        from_state = str(row["state"]).upper()
        allowed = _TRANSITIONS.get(from_state)
        if allowed is None:
            raise ValueError(f"Unknown current state: {from_state}")
        if to_state not in allowed:
            raise ValueError(f"Invalid transition: {from_state} -> {to_state}")

        with conn:
            conn.execute(
                "UPDATE tracks SET state = ?, updated_at = datetime('now') WHERE id = ?",
                (to_state, track_id),
            )
            conn.execute(
                """INSERT INTO release_states
                   (track_id, from_state, to_state, changed_by, reason)
                   VALUES (?, ?, ?, ?, ?)""",
                (track_id, from_state, to_state, changed_by, reason),
            )
            updated = conn.execute(
                """SELECT id, title, file_path, file_hash, file_size,
                          duration_seconds, format, parent_track_id, version,
                          state, project_id, created_at, updated_at
                   FROM tracks WHERE id = ?""",
                (track_id,),
            ).fetchone()
    return dict(updated)


def _vault_track(*, track_id: int, reason: str | None = None) -> dict:
    reason = (reason or "Moved to vault").strip()
    if not reason:
        raise ValueError("reason cannot be empty")
    with _db_conn() as conn:
        row = conn.execute("SELECT id, state FROM tracks WHERE id = ?", (track_id,)).fetchone()
        if row is None:
            raise ValueError(f"Track {track_id} not found")
        from_state = str(row["state"]).upper()
        if from_state == "VAULT":
            return _track_payload(conn, track_id)
        with conn:
            conn.execute(
                """UPDATE tracks
                   SET state = 'VAULT',
                       vault_reason = ?,
                       vault_date = datetime('now'),
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (reason, track_id),
            )
            conn.execute(
                """INSERT INTO release_states
                   (track_id, from_state, to_state, changed_by, reason)
                   VALUES (?, ?, 'VAULT', 'artist', ?)""",
                (track_id, from_state, reason),
            )
        return _track_payload(conn, track_id)


def _delete_track_tracking(*, track_id: int, delete_file: bool = False) -> dict:
    with _db_conn() as conn:
        row = conn.execute("SELECT id, file_path FROM tracks WHERE id = ?", (track_id,)).fetchone()
        if row is None:
            raise ValueError(f"Track {track_id} not found")
        file_path = str(row["file_path"])
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        track_id_tables: list[str] = []
        for table in tables:
            table_name = str(table["name"])
            if table_name == "tracks":
                continue
            columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            if any(str(col["name"]) == "track_id" for col in columns):
                track_id_tables.append(table_name)

        with conn:
            conn.execute(
                "UPDATE tracks SET parent_track_id = NULL WHERE parent_track_id = ?",
                (track_id,),
            )
            deleted_children: dict[str, int] = {}
            for table_name in track_id_tables:
                cur = conn.execute(f"DELETE FROM {table_name} WHERE track_id = ?", (track_id,))
                if cur.rowcount:
                    deleted_children[table_name] = cur.rowcount
            cur = conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
            if cur.rowcount != 1:
                raise ValueError(f"Track {track_id} was not deleted")

        file_deleted = False
        if delete_file:
            path = Path(file_path)
            if path.exists():
                path.unlink()
                file_deleted = True
        return {
            "deleted": True,
            "track_id": track_id,
            "file_deleted": file_deleted,
            "deleted_children": deleted_children,
        }


def _log_artist_message(
    *,
    agent: str,
    message: str,
    track_id: int | None,
    timestamp_sec: float | None = None,
) -> dict:
    allowed_agents = {"a_and_r", "manager", "creative_director", "bandcamp"}
    if agent not in allowed_agents:
        raise ValueError(f"Unknown agent: {agent}")
    if not message.strip():
        raise ValueError("Message cannot be empty")

    with _db_conn() as conn:
        if track_id is not None:
            exists = conn.execute("SELECT 1 FROM tracks WHERE id = ?", (track_id,)).fetchone()
            if exists is None:
                raise ValueError(f"Track {track_id} not found")
        with conn:
            cursor = conn.execute(
                """INSERT INTO feedback
                   (track_id, project_id, agent, message, channel, direction, intent, timestamp_sec)
                   VALUES (?, NULL, ?, ?, 'desktop', 'inbound', 'question', ?)""",
                (track_id, agent, message.strip(), timestamp_sec),
            )
            message_id = cursor.lastrowid
            row = conn.execute(
                """SELECT id, track_id, project_id, agent, message, channel,
                          direction, intent, timestamp_sec, created_at
                   FROM feedback WHERE id = ?""",
                (message_id,),
            ).fetchone()

    def run_roundtable_reply() -> None:
        try:
            _fire_event(
                "artist_message_inbound",
                {"agent": agent, "message": message.strip(), "track_id": track_id},
            )
        except Exception:
            logger.exception("Failed to fire artist_message_inbound event")

    threading.Thread(
        target=run_roundtable_reply,
        daemon=True,
        name=f"artist-message-{message_id}",
    ).start()

    return dict(row)


_DEFAULT_SETTINGS: dict[str, Any] = {
    "ableton_project_folder": "",
    "ableton_export_folder": "",
    "artist_name": "",
    "artist_phone": "",
    "quiet_hours_start": "22:00",
    "quiet_hours_end": "09:00",
    "quiet_days": [],
    "dnd_enabled": False,
    "voice_provider": "elevenlabs",
    "fish_voice_map": {},
}


def _read_settings() -> dict[str, Any]:
    result = dict(_DEFAULT_SETTINGS)
    settings_path = _DATA_DIR / "settings.json"
    if settings_path.exists():
        with suppress(json.JSONDecodeError, OSError):
            result.update(json.loads(settings_path.read_text()))
    return result


def _write_settings(data: dict[str, Any]) -> None:
    settings_path = _DATA_DIR / "settings.json"
    settings_path.write_text(json.dumps(data, indent=2))


# Pipeline runs (analysis, stem separation, segments, embeddings, agent
# reviews) do many sequential writes against a single-writer SQLite database.
# Running two in parallel — e.g. a manual intake overlapping the timeout
# scanner's automatic retry of a previously-stuck track — only makes both
# slower as they contend for the same write lock. There's no concurrency
# benefit to gain, so serialize pipeline execution instead.
_pipeline_lock = threading.Lock()


def _fire_event(event: str, payload: dict[str, Any]) -> None:
    """Dispatch *event* through session intelligence and coordination."""
    from coordination.dispatcher import TrackPipelineDispatcher
    from session_intelligence.watcher_integration import (
        SessionIntelligenceEmitter,  # type: ignore[import-untyped]
    )

    SessionIntelligenceEmitter(db_path=DB_PATH)(event, payload)
    with _pipeline_lock:
        TrackPipelineDispatcher(db_path=DB_PATH)(event, payload)


def _kick_debate(track_id: int) -> dict:
    """Fire an agent-to-agent debate in the background; returns immediately."""

    def run_debate() -> None:
        try:
            _fire_event(
                "agent_debate_requested",
                {"track_id": track_id},
            )
        except Exception:
            logger.exception("Failed to fire agent_debate_requested event")

    threading.Thread(target=run_debate, daemon=True, name=f"agent-debate-{track_id}").start()
    return {"status": "started", "track_id": track_id}


# ── Intake helpers ────────────────────────────────────────────────────────────

_AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aiff", ".aif", ".ogg", ".m4a"}
_INBOX_DIR: Path = _DATA_DIR / "inbox"
_INBOX_DIR.mkdir(parents=True, exist_ok=True)
_MAX_INTAKE_BYTES = int(os.environ.get("MAX_INTAKE_UPLOAD_BYTES", str(512 * 1024 * 1024)))
_SCRIPTS_DIR: Path = Path(__file__).resolve().parent / "scripts"


def _sha256_of_path(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_audio_metadata(path: Path) -> dict:
    """Read ID3/FLAC/MP4 tags via mutagen. Falls back to filename."""
    meta: dict = {
        "title": path.stem,
        "duration_seconds": None,
        "artist": None,
        "album": None,
        "tracknumber": None,
        "date": None,
    }
    try:
        from mutagen import File as MutagenFile  # type: ignore[import]

        audio = MutagenFile(path, easy=True)
        if audio:

            def _first(key: str) -> str | None:
                val = audio.get(key)
                return val[0] if val else None

            meta["title"] = _first("title") or path.stem
            meta["artist"] = _first("artist")
            meta["album"] = _first("album")
            meta["tracknumber"] = _first("tracknumber")
            meta["date"] = _first("date")
            if hasattr(audio, "info") and hasattr(audio.info, "length"):
                meta["duration_seconds"] = audio.info.length
    except ImportError:
        pass
    except Exception:
        pass
    return meta


def _launch_b2_sync() -> None:
    """Kick off scripts/sync_to_cloud.sh in the background. Non-blocking."""
    script = _SCRIPTS_DIR / "sync_to_cloud.sh"
    if not script.is_file():
        logger.warning("sync_to_cloud.sh not found at %s — skipping B2 sync", script)
        return
    try:
        subprocess.Popen(
            ["bash", str(script)],
            env={**os.environ},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(_SCRIPTS_DIR.parent),
        )
    except Exception:
        logger.exception("Failed to launch B2 sync")


def _intake_files(
    saved_files: list[Path],
    album_title_override: str | None,
    project_type: str = "album",
    state: str = "DRAFT",
) -> dict:
    """Create project + track rows for a list of audio files already on disk.

    Mirrors the logic in scripts/intake_album.py but runs inline.
    """
    if not saved_files:
        raise ValueError("No audio files provided")

    # Determine album title from most common ID3 album tag, falling back to override / parent dir
    per_file_meta = [_read_audio_metadata(p) for p in saved_files]
    album_tags = [m.get("album") for m in per_file_meta if m.get("album")]
    if album_title_override:
        album_title = album_title_override
    elif album_tags:
        album_title = collections.Counter(album_tags).most_common(1)[0][0]
    else:
        album_title = saved_files[0].parent.name or "Untitled"

    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO projects (title, type, state, target_track_count)
               VALUES (?, ?, 'active', ?)""",
            (album_title, project_type, len(saved_files)),
        )
        project_id = cur.lastrowid
        conn.commit()

        track_ids: list[int] = []
        skipped_duplicates = 0

        for audio_file, meta in zip(saved_files, per_file_meta, strict=True):
            file_hash = _sha256_of_path(audio_file)
            file_size = audio_file.stat().st_size

            existing = conn.execute(
                "SELECT id FROM tracks WHERE file_hash = ?", (file_hash,)
            ).fetchone()
            if existing:
                skipped_duplicates += 1
                continue

            cur = conn.execute(
                """INSERT INTO tracks
                   (title, file_path, file_hash, file_size, duration_seconds, format,
                    version, state, project_id)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    meta["title"],
                    str(audio_file),
                    file_hash,
                    file_size,
                    meta.get("duration_seconds"),
                    audio_file.suffix.lstrip(".").lower(),
                    state,
                    project_id,
                ),
            )
            if cur.lastrowid is not None:
                track_ids.append(cur.lastrowid)
            conn.commit()
    finally:
        conn.close()

    return {
        "project_id": project_id,
        "album": album_title,
        "tracks_added": len(track_ids),
        "skipped_duplicates": skipped_duplicates,
        "track_ids": track_ids,
    }


def _intake_local_folder(folder_path: str) -> dict:
    """Run scripts/intake_album.py --json for a local folder path."""
    folder = Path(folder_path)
    if not folder.is_dir():
        raise ValueError(f"Not a directory: {folder_path}")

    script = _SCRIPTS_DIR / "intake_album.py"
    if not script.is_file():
        raise FileNotFoundError(f"intake_album.py not found at {script}")

    proc = subprocess.run(
        [sys.executable, str(script), str(folder), "--no-copy", "--json"],
        capture_output=True,
        text=True,
        env={**os.environ},
        timeout=600,
    )
    stderr_out = proc.stderr or ""
    stdout_out = proc.stdout or ""
    if proc.returncode != 0:
        raise RuntimeError(f"intake_album.py exited {proc.returncode}: {stderr_out or stdout_out}")
    if not stdout_out.strip():
        raise RuntimeError(f"intake_album.py produced no JSON output. stderr: {stderr_out}")
    try:
        result = json.loads(stdout_out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"intake_album.py output is not valid JSON: {exc}\nraw: {stdout_out!r}"
        ) from exc

    required = {"project_id", "album", "tracks_added", "skipped_duplicates", "track_ids"}
    missing = required - result.keys()
    if missing:
        raise RuntimeError(f"intake_album.py JSON missing fields {missing}: {result}")
    return result


def _bpm_bucket(bpm: float | None) -> str:
    """Map a BPM value to a display bucket label."""
    if bpm is None:
        return "unknown BPM"
    if bpm < 70:
        return "< 70 BPM"
    if bpm < 90:
        return "70-90 BPM"
    if bpm < 110:
        return "90-110 BPM"
    if bpm < 130:
        return "110-130 BPM"
    if bpm < 150:
        return "130-150 BPM"
    return "150+ BPM"


_INSTRUMENT_FAMILIES = (
    "kick",
    "snare",
    "hi-hat",
    "bass",
    "piano",
    "rhodes",
    "keys",
    "vocal",
    "guitar",
    "synth",
    "strings",
    "drums",
    "pad",
    "trumpet",
    "saxophone",
    "violin",
    "sample",
    "vinyl",
)

# Agent labels shown as graph nodes
_AGENT_LABELS: dict[str, str] = {
    "kallman": "Kallman",
    "a_and_r": "A&R",
    "janick": "Janick",
    "rhone": "Rhone",
    "rubin": "Rubin",
    "creative_director": "Maren",
    "manager": "Dez",
}

# Feedback intents that represent a meaningful agent take on a track
_AGENT_REVIEW_INTENTS = frozenset(
    {
        "early_conviction_feedback",
        "analysis_feedback",
        "vision_assessment",
        "cultural_authenticity_read",
        "essential_question_review",
        "a_and_r_feedback",
        "review_round_summary",
    }
)


def _norm_instrument(raw: str) -> str | None:
    """Map a free-text instrument description to a canonical family name."""
    norm = raw.lower()
    for family in _INSTRUMENT_FAMILIES:
        if family in norm:
            return family
    return None


def _energy_level(loudness_rms: float | None) -> str | None:
    """Bucket mean RMS loudness into a display tier."""
    if loudness_rms is None:
        return None
    if loudness_rms < 0.04:
        return "low energy"
    if loudness_rms < 0.12:
        return "mid energy"
    return "high energy"


def _rhythm_feel(
    swing_ratio: float | None,
    tempo_var: float | None,
) -> str | None:
    """Classify the rhythmic feel of a track."""
    if swing_ratio is None and tempo_var is None:
        return None
    swing = swing_ratio or 1.0
    var = tempo_var or 0.0
    if var > 0.20:
        return "loose tempo"
    if swing > 1.22:
        return "swung"
    return "straight"


def _texture(
    flatness: float | None,
    harmonic_ratio: float | None,
) -> str | None:
    """Classify the tonal texture of a track."""
    if flatness is None and harmonic_ratio is None:
        return None
    flat = flatness or 0.0
    harm = harmonic_ratio if harmonic_ratio is not None else 0.5
    if flat < 0.04 and harm > 0.60:
        return "tonal"
    if flat > 0.15 or harm < 0.30:
        return "noisy"
    return "mixed"


def _build_insights_graph() -> dict:
    """Build nodes + links for the knowledge-graph web.

    Node types:
      track        — one per track with segment data or audio features
      mood         — one per unique mood across all segments
      element      — sonic element family from segment analysis
      key          — musical key detected by librosa
      bpm          — BPM bucket (< 70, 70-90, 90-110, etc.)
      section      — structural section label
      genre        — genre tag from Gemini audio analysis
      instrument   — instrument family from Gemini audio analysis
      subgenre     — subgenre from artist profile
      agent        — agent who gave a meaningful review of this track
      verdict      — roundtable recommendation (SHIP / REVISE / VAULT / MINE_FOR_LOOPS)
      mode         — major or minor key (from extended librosa features)
      energy_level — low / mid / high energy (from loudness RMS)
      rhythm_feel  — straight / swung / loose tempo (from madmom + tempo variability)
      texture      — tonal / mixed / noisy (from spectral flatness + HPSS)

    Links connect each track node to its associated category nodes.
    """
    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        tracks = conn.execute(
            """
            SELECT DISTINCT t.id, t.title, t.state
              FROM tracks t
             WHERE EXISTS (SELECT 1 FROM track_segments ts   WHERE ts.track_id = t.id)
                OR EXISTS (SELECT 1 FROM track_audio_features af WHERE af.track_id = t.id)
                OR EXISTS (SELECT 1 FROM audio_analyses aa   WHERE aa.track_id = t.id)
            ORDER BY t.id ASC
            """
        ).fetchall()

        segments = conn.execute(
            "SELECT track_id, section_label, mood, elements_present FROM track_segments"
        ).fetchall()

        audio_features = conn.execute(
            """
            SELECT track_id, musical_key, bpm, mode,
                   loudness_rms, madmom_swing_ratio, tempo_variability,
                   spectral_flatness_mean, hpss_harmonic_ratio
              FROM track_audio_features
            """
        ).fetchall()

        audio_analyses = conn.execute(
            "SELECT track_id, genre_tags, instruments FROM audio_analyses"
        ).fetchall()

        # Agent feedback — only intents that represent a real review take
        agent_feedback = conn.execute(
            """
            SELECT DISTINCT track_id, agent
              FROM feedback
             WHERE direction = 'outbound'
               AND intent IN ({})
               AND track_id IS NOT NULL
            """.format(",".join("?" * len(_AGENT_REVIEW_INTENTS))),
            tuple(_AGENT_REVIEW_INTENTS),
        ).fetchall()

        verdicts = conn.execute(
            """
            SELECT track_id, recommendation
              FROM roundtable_verdicts
             WHERE superseded_at IS NULL
            """
        ).fetchall()

        # Artist profile subgenres (shared across all tracks from this artist)
        profile = conn.execute("SELECT subgenres FROM artist_profile LIMIT 1").fetchone()
        artist_subgenres: list[str] = []
        if profile and profile["subgenres"]:
            artist_subgenres = [
                s.strip().lower() for s in profile["subgenres"].split(",") if s.strip()
            ]
    finally:
        conn.close()

    nodes: list[dict] = []
    links: list[dict] = []
    seen_nodes: set[str] = set()
    seen_links: set[tuple[str, str]] = set()

    def _add_node(node_id: str, node_type: str, label: str) -> None:
        if node_id not in seen_nodes:
            nodes.append({"id": node_id, "type": node_type, "label": label})
            seen_nodes.add(node_id)

    def _add_link(source: str, target: str) -> None:
        key = (source, target)
        if key not in seen_links:
            links.append({"source": source, "target": target})
            seen_links.add(key)

    # ── Track nodes ───────────────────────────────────────────────────────────
    track_ids: set[int] = set()
    for t in tracks:
        tid = f"track:{t['id']}"
        _add_node(tid, "track", t["title"] or f"Track {t['id']}")
        track_ids.add(t["id"])

    # ── Segment-derived: mood / element / section ─────────────────────────────
    track_moods: dict[int, set[str]] = {}
    track_elements: dict[int, set[str]] = {}
    track_sections: dict[int, set[str]] = {}

    for seg in segments:
        tid = seg["track_id"]
        track_moods.setdefault(tid, set())
        track_elements.setdefault(tid, set())
        track_sections.setdefault(tid, set())

        if seg["mood"]:
            track_moods[tid].add(seg["mood"].lower().strip())
        if seg["section_label"]:
            track_sections[tid].add(seg["section_label"].lower().strip())
        if seg["elements_present"]:
            try:
                for e in json.loads(seg["elements_present"]):
                    family = _norm_instrument(e)
                    if family:
                        track_elements[tid].add(family)
            except (TypeError, json.JSONDecodeError):
                pass

    for tid, moods in track_moods.items():
        for mood in moods:
            nid = f"mood:{mood}"
            _add_node(nid, "mood", mood)
            _add_link(f"track:{tid}", nid)

    for tid, sections in track_sections.items():
        for section in sections:
            nid = f"section:{section}"
            _add_node(nid, "section", section)
            _add_link(f"track:{tid}", nid)

    for tid, elements in track_elements.items():
        for elem in elements:
            nid = f"element:{elem}"
            _add_node(nid, "element", elem)
            _add_link(f"track:{tid}", nid)

    # ── Audio features: key / bpm / mode / energy / feel / texture ───────────
    for af in audio_features:
        tid = af["track_id"]
        if af["musical_key"]:
            nid = f"key:{af['musical_key']}"
            _add_node(nid, "key", af["musical_key"])
            _add_link(f"track:{tid}", nid)
        bucket = _bpm_bucket(af["bpm"])
        nid = f"bpm:{bucket}"
        _add_node(nid, "bpm", bucket)
        _add_link(f"track:{tid}", nid)
        # mode (major / minor)
        if af["mode"]:
            nid = f"mode:{af['mode']}"
            _add_node(nid, "mode", af["mode"].title())
            _add_link(f"track:{tid}", nid)
        # energy level (low / mid / high)
        energy = _energy_level(af["loudness_rms"])
        if energy:
            nid = f"energy:{energy}"
            _add_node(nid, "energy_level", energy)
            _add_link(f"track:{tid}", nid)
        # rhythm feel (straight / swung / loose tempo)
        feel = _rhythm_feel(af["madmom_swing_ratio"], af["tempo_variability"])
        if feel:
            nid = f"feel:{feel}"
            _add_node(nid, "rhythm_feel", feel)
            _add_link(f"track:{tid}", nid)
        # tonal texture (tonal / mixed / noisy)
        tex = _texture(af["spectral_flatness_mean"], af["hpss_harmonic_ratio"])
        if tex:
            nid = f"texture:{tex}"
            _add_node(nid, "texture", tex)
            _add_link(f"track:{tid}", nid)

    # ── Audio analyses (Gemini): genre / instrument ───────────────────────────
    for aa in audio_analyses:
        tid = aa["track_id"]
        if aa["genre_tags"]:
            try:
                for tag in json.loads(aa["genre_tags"]):
                    genre = tag.lower().strip()
                    nid = f"genre:{genre}"
                    _add_node(nid, "genre", tag.title())
                    _add_link(f"track:{tid}", nid)
            except (TypeError, json.JSONDecodeError):
                pass
        if aa["instruments"]:
            try:
                for raw in json.loads(aa["instruments"]):
                    family = _norm_instrument(raw)
                    if family:
                        nid = f"instrument:{family}"
                        _add_node(nid, "instrument", family)
                        _add_link(f"track:{tid}", nid)
            except (TypeError, json.JSONDecodeError):
                pass

    # ── Artist profile subgenres (apply to all tracks) ────────────────────────
    for subgenre in artist_subgenres:
        nid = f"subgenre:{subgenre}"
        _add_node(nid, "subgenre", subgenre.title())
        for tid in track_ids:
            _add_link(f"track:{tid}", nid)

    # ── Agent feedback ────────────────────────────────────────────────────────
    for af in agent_feedback:
        agent = af["agent"]
        label = _AGENT_LABELS.get(agent, agent)
        nid = f"agent:{agent}"
        _add_node(nid, "agent", label)
        _add_link(f"track:{af['track_id']}", nid)

    # ── Verdicts ──────────────────────────────────────────────────────────────
    for v in verdicts:
        rec = v["recommendation"]
        nid = f"verdict:{rec}"
        _add_node(nid, "verdict", rec.replace("_", " ").title())
        _add_link(f"track:{v['track_id']}", nid)

    return {"nodes": nodes, "links": links}


def _get_intake_status(project_id: int) -> dict | None:
    """Return project + tracks (with has_analysis flag) for the status endpoint."""
    with _db_conn() as conn:
        proj = conn.execute("SELECT id, title FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not proj:
            return None
        tracks = conn.execute(
            "SELECT id, title, state FROM tracks WHERE project_id = ? ORDER BY id ASC",
            (project_id,),
        ).fetchall()

        has_analysis_table = _table_exists(conn, "audio_analyses")
        track_list = []
        for t in tracks:
            has_analysis = False
            if has_analysis_table:
                row = conn.execute(
                    "SELECT 1 FROM audio_analyses WHERE track_id = ? LIMIT 1",
                    (t["id"],),
                ).fetchone()
                has_analysis = row is not None
            track_list.append(
                {
                    "id": t["id"],
                    "title": t["title"],
                    "state": t["state"],
                    "has_analysis": has_analysis,
                }
            )

    return {
        "project_id": proj["id"],
        "title": proj["title"],
        "tracks": track_list,
    }


# ── HTTP handler ───────────────────────────────────────────────────────────────

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
}


class RecordLabelHandler(BaseHTTPRequestHandler):
    # Silence default "127.0.0.1 - - [date] GET /path HTTP/1.1 200 -" lines;
    # we log ourselves at DEBUG level.
    def log_message(self, fmt: str, *args: Any) -> None:  # type: ignore[override]
        logger.debug(fmt, *args)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str = "application/json",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: Any) -> None:
        body = json.dumps(obj, default=str).encode()
        self._send(status, body)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _authenticated(self) -> bool:
        auth = self.headers.get("Authorization", "")
        expected = f"Bearer {_load_or_create_token()}"
        return hmac.compare_digest(auth, expected)

    def _local_token_bootstrap_allowed(self) -> bool:
        """Only expose the bootstrap token to this app's local browser/Tauri origins."""
        try:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                return False
        except ValueError:
            return False
        host_header = self.headers.get("Host", "").strip().lower()
        host = (
            host_header.split("]", 1)[0] + "]"
            if host_header.startswith("[")
            else host_header.split(":", 1)[0]
        )
        if host not in {"localhost", "127.0.0.1", "[::1]"}:
            return False
        origin = self.headers.get("Origin", "").strip().lower()
        return not origin or origin in {
            "http://localhost:8086",
            "http://127.0.0.1:8086",
            "tauri://localhost",
            "http://tauri.localhost",
        }

    def _parse_url(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urlparse(self.path)
        return parsed.path, parse_qs(parsed.query)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    # ── /api/intake ─────────────────────────────────────────────────────────

    def _handle_intake(self) -> None:
        content_type = self.headers.get("Content-Type", "") or ""

        try:
            if content_type.startswith("multipart/form-data"):
                result = self._handle_intake_multipart()
            elif content_type.startswith("application/json"):
                result = self._handle_intake_local_path()
            else:
                self._error(
                    400,
                    "Content-Type must be multipart/form-data or application/json",
                )
                return

            if result is None:
                return  # error already sent

            track_ids: list[int] = [int(tid) for tid in result["track_ids"]]
            if not track_ids and result.get("tracks_added", 0) > 0:
                raise RuntimeError(
                    f"Intake registered {result['tracks_added']} track(s) but "
                    "returned no track_ids - pipeline cannot start"
                )

            # Registration is complete, so answer the browser without waiting for
            # slow analysis/model calls. The background worker still drives the
            # real state transitions and agent actions for every accepted track.
            def run_pipeline() -> None:
                try:
                    for tid in track_ids:
                        _fire_event(
                            "new_track_detected",
                            {"track_id": tid, "folder": result["album"]},
                        )
                    if len(track_ids) > 1:
                        _fire_event(
                            "catalog_memory_refresh",
                            {"project_id": int(result["project_id"])},
                        )
                except Exception:
                    logger.exception("Failed to fire pipeline events after intake")

            if track_ids:
                threading.Thread(
                    target=run_pipeline,
                    daemon=True,
                    name=f"intake-pipeline-{result['project_id']}",
                ).start()

            # Background B2 sync
            _launch_b2_sync()

            self._json(
                200,
                {
                    "status": "ok",
                    "project_id": result["project_id"],
                    "tracks_added": result["tracks_added"],
                    "album": result["album"],
                    "skipped_duplicates": result["skipped_duplicates"],
                    "pipeline_started": bool(track_ids),
                },
            )
        except Exception as exc:
            logger.exception("Intake failed")
            self._error(500, f"Intake failed: {exc}")

    def _handle_intake_multipart(self) -> dict | None:
        content_type = self.headers.get("Content-Type", "")
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._error(400, "Invalid Content-Length")
            return None
        if content_length <= 0:
            self._error(400, "Empty upload")
            return None
        if content_length > _MAX_INTAKE_BYTES:
            self._error(413, f"Upload exceeds {_MAX_INTAKE_BYTES} byte limit")
            return None
        body = self.rfile.read(content_length)

        try:
            parsed_parts = _parse_multipart_parts(content_type, body)
        except Exception as exc:
            self._error(400, f"Failed to parse multipart body: {exc}")
            return None

        album_override: str | None = None
        file_items: list[tuple[str, bytes]] = []  # (filename, data)

        for part in parsed_parts:
            if part["filename"]:
                file_items.append((os.path.basename(part["filename"]), part["data"]))
            elif part["name"] == "album_name":
                album_override = part["data"].decode("utf-8", errors="replace").strip() or None

        if not file_items:
            self._error(400, "No files uploaded")
            return None

        _INBOX_DIR.mkdir(parents=True, exist_ok=True)

        # Pre-fetch existing hashes for dedup
        with _db_conn() as conn:
            existing_hashes = {
                r[0] for r in conn.execute("SELECT file_hash FROM tracks").fetchall()
            }

        saved_files: list[Path] = []
        skipped_pre = 0
        for filename, data in file_items:
            if not filename:
                continue
            suffix = Path(filename).suffix.lower()
            if suffix not in _AUDIO_EXTENSIONS:
                continue

            if not isinstance(data, bytes) or not data:
                continue

            file_hash = _sha256_of_bytes(data)
            if file_hash in existing_hashes:
                skipped_pre += 1
                continue
            existing_hashes.add(file_hash)

            dest = _INBOX_DIR / filename
            if dest.exists():
                dest = _INBOX_DIR / f"{file_hash[:8]}_{filename}"
            with open(dest, "wb") as f:
                f.write(data)
            saved_files.append(dest)

        if not saved_files:
            if skipped_pre:
                # Nothing new — return informative response (caller treats as ok)
                return {
                    "project_id": 0,
                    "album": album_override or "",
                    "tracks_added": 0,
                    "skipped_duplicates": skipped_pre,
                    "track_ids": [],
                }
            self._error(400, "No valid audio files in upload")
            return None

        result = _intake_files(saved_files, album_override)
        result["skipped_duplicates"] += skipped_pre
        return result

    def _handle_intake_local_path(self) -> dict | None:
        raw = self._read_body()
        if not raw:
            self._error(400, "Empty request body")
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._error(400, f"Invalid JSON: {exc}")
            return None
        if not isinstance(data, dict):
            self._error(400, "Request body must be a JSON object")
            return None
        folder_path = data.get("folder_path")
        if not folder_path or not isinstance(folder_path, str):
            self._error(400, "'folder_path' required (absolute path string)")
            return None
        if not os.path.isabs(folder_path):
            self._error(400, "'folder_path' must be absolute")
            return None
        if not Path(folder_path).is_dir():
            self._error(400, f"Not a directory: {folder_path}")
            return None
        return _intake_local_folder(folder_path)

    # ── /stt ─────────────────────────────────────────────────────────────────

    def _handle_stt(self) -> None:
        content_type = self.headers.get("Content-Type", "") or ""
        if not content_type.startswith("multipart/form-data"):
            self._error(400, "Content-Type must be multipart/form-data")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            parts = _parse_multipart_parts(content_type, body)
        except Exception as exc:
            self._error(400, f"Failed to parse multipart body: {exc}")
            return

        file_part = next((p for p in parts if p["name"] == "file"), None)
        if file_part is None or not file_part["data"]:
            self._error(400, "'file' field required")
            return

        from audio_analysis.stt import SttError, transcribe

        try:
            text = transcribe(
                file_part["data"],
                file_part["filename"] or "utterance.webm",
                file_part["content_type"],
            )
        except SttError as exc:
            self._error(502, str(exc))
            return

        self._json(200, {"text": text})

    # ── OPTIONS (CORS preflight) ────────────────────────────────────────────

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ── Static file serving ──────────────────────────────────────────────

    def _serve_static(self, path: str) -> None:
        """Serve static files from the Vite build directory (SPA fallback)."""
        if not STATIC_DIR.is_dir():
            self._error(404, "Web UI not built. Run: cd desktop-app && npm run build")
            return

        # Map URL path to filesystem path
        rel = path.lstrip("/")
        if not rel:
            rel = "index.html"
        file_path = STATIC_DIR / rel

        # Security: prevent path traversal
        try:
            file_path = file_path.resolve()
            if not str(file_path).startswith(str(STATIC_DIR.resolve())):
                self._error(403, "Forbidden")
                return
        except (OSError, ValueError):
            self._error(400, "Bad path")
            return

        # If file doesn't exist, serve index.html (SPA fallback for React Router)
        if not file_path.is_file():
            file_path = STATIC_DIR / "index.html"
            if not file_path.is_file():
                self._error(404, "index.html not found — web UI not built")
                return

        # Determine content type
        suffix = file_path.suffix.lower()
        _EXTRA_TYPES = {
            # Windows commonly reports .mjs as text/plain, which browsers
            # reject for ONNX Runtime's module worker.
            ".mjs": "application/javascript",
            ".woff2": "font/woff2",
            ".woff": "font/woff",
            ".ttf": "font/ttf",
            ".otf": "font/otf",
            ".webp": "image/webp",
            ".avif": "image/avif",
            ".webm": "video/webm",
        }
        content_type = _EXTRA_TYPES.get(suffix)
        if content_type is None:
            content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"

        # Read and send the file
        try:
            body = file_path.read_bytes()
        except OSError:
            self._error(500, "Failed to read file")
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Cache headers: Vite hashed assets are immutable; everything else no-cache
        if "/assets/" in path:
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        else:
            self.send_header("Cache-Control", "no-cache")
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    # ── GET ─────────────────────────────────────────────────────────────────

    # API paths that require authentication (checked before static fallback)
    _API_PATHS = frozenset(
        {
            "/token",
            "/tracks",
            "/tracks/vault",
            "/tracks/delete",
            "/stats",
            "/feedback",
            "/sessions",
            "/export_events",
            "/artist_profile",
            "/projects",
            "/track_audio",
            "/release_states",
            "/settings",
            "/api/intake",
            "/artist_message",
            "/verdict",
            "/segments",
            "/audio_features",
            "/insights/graph",
            "/wave_vault",
            "/artwork/generations",
            "/artwork/image",
            "/tts",
        }
    )

    def do_GET(self) -> None:
        path, qs = self._parse_url()

        # Unauthenticated endpoint
        if path == "/health":
            self._send(200, b"ok", "text/plain")
            return

        if path == "/token":
            if not self._local_token_bootstrap_allowed():
                self._error(403, "Local token bootstrap is not allowed from this origin")
                return
            if self.headers.get("Authorization") and not self._authenticated():
                self._error(401, "Unauthorized")
                return
            self._json(200, {"token": _load_or_create_token()})
            return

        # Check if this is an API route (requires auth) or static file (public).
        # API routes are only activated when a Bearer token is present — this
        # lets the browser navigate to e.g. /tracks and receive index.html (SPA),
        # while JS fetch() calls with Authorization headers get the JSON data.
        is_api = (path in self._API_PATHS or path.startswith("/api/")) and self._authenticated()
        if not is_api and (path in self._API_PATHS or path.startswith("/api/")):
            # Path looks like an API route but no valid auth — serve SPA so the
            # browser gets the React app's auth gate rather than a bare 401.
            self._serve_static(path)
            return

        try:
            if path == "/tracks":
                self._json(200, _get_tracks())

            elif path == "/stats":
                self._json(200, _get_stats())

            elif path == "/feedback":
                raw_id = qs.get("track_id", [None])[0]
                if raw_id is None:
                    self._error(400, "track_id query param required")
                    return
                try:
                    track_id = int(raw_id)
                except ValueError:
                    self._error(400, "track_id must be an integer")
                    return
                self._json(200, _get_feedback(track_id))

            elif path == "/track_audio":
                raw_id = qs.get("track_id", [None])[0]
                if raw_id is None:
                    self._error(400, "track_id query param required")
                    return
                try:
                    track_id = int(raw_id)
                except ValueError:
                    self._error(400, "track_id must be an integer")
                    return
                audio_path = _get_track_audio_path(track_id)
                if audio_path is None:
                    self._error(404, f"Track {track_id} not found")
                    return
                if not audio_path.exists() or not audio_path.is_file():
                    self._error(404, f"Audio file missing for track {track_id}")
                    return
                content_type = (
                    mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
                )
                body = audio_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                for k, v in CORS_HEADERS.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)
                return

            elif path == "/tts":
                raw_id = qs.get("message_id", [None])[0]
                if raw_id is None:
                    self._error(400, "message_id query param required")
                    return
                try:
                    message_id = int(raw_id)
                except ValueError:
                    self._error(400, "message_id must be an integer")
                    return
                feedback_row = _get_feedback_by_id(message_id)
                if feedback_row is None:
                    self._error(404, f"Message {message_id} not found")
                    return
                from audio_analysis.tts import TtsError, cached_media_type, synthesize

                try:
                    audio_path = synthesize(
                        _DATA_DIR, message_id, feedback_row["agent"], feedback_row["message"]
                    )
                except TtsError as exc:
                    self._error(502, str(exc))
                    return
                body = audio_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", cached_media_type(audio_path))
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "max-age=31536000, immutable")
                for k, v in CORS_HEADERS.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)
                return

            elif path == "/sessions":
                raw_limit = qs.get("limit", ["20"])[0]
                try:
                    limit = int(raw_limit)
                except ValueError:
                    limit = 20
                self._json(200, _get_sessions(limit))

            elif path == "/export_events":
                raw_limit = qs.get("limit", ["20"])[0]
                try:
                    limit = int(raw_limit)
                except ValueError:
                    limit = 20
                self._json(200, _get_export_events(limit))

            elif path == "/voice/library":
                import httpx

                from audio_analysis.tts import _env_key

                key = _env_key("FISH_API_KEY")
                if not key:
                    self._error(400, "FISH_API_KEY not set")
                    return
                try:
                    resp = httpx.get(
                        "https://api.fish.audio/model",
                        params={"page_size": 60, "page_number": 1, "sort_by": "task_count"},
                        headers={"Authorization": f"Bearer {key}"},
                        timeout=20.0,
                    )
                    resp.raise_for_status()
                    items = [
                        {
                            "id": item.get("_id"),
                            "title": item.get("title", ""),
                            "task_count": item.get("task_count", 0),
                        }
                        for item in resp.json().get("items", [])
                        if item.get("_id")
                    ]
                    self._json(200, {"voices": items})
                except Exception as exc:
                    self._error(502, f"Fish voice library unavailable: {exc}")

            elif path == "/artist_profile":
                self._json(200, _get_artist_profile())

            elif path == "/voice/status":
                from audio_analysis.tts import (
                    FISH_LOCAL_BASE_URL,
                    _env_key,
                    local_server_ready,
                )

                settings = _read_settings()
                self._json(
                    200,
                    {
                        "provider": settings.get("voice_provider", "elevenlabs"),
                        "cloud_key_set": bool(_env_key("FISH_API_KEY")),
                        "elevenlabs_key_set": bool(_env_key("ELEVENLABS_API_KEY")),
                        "local_ready": local_server_ready(timeout=0.75),
                        "local_url": FISH_LOCAL_BASE_URL,
                        "gpu_vram_mb": _gpu_total_vram_mb(),
                    },
                )

            elif path == "/projects":
                self._json(200, _get_projects())

            elif path == "/release_states":
                raw_id = qs.get("track_id", [None])[0]
                if raw_id is None:
                    self._error(400, "track_id query param required")
                    return
                try:
                    track_id = int(raw_id)
                except ValueError:
                    self._error(400, "track_id must be an integer")
                    return
                self._json(200, _get_release_states(track_id))

            elif path == "/verdict":
                raw_id = qs.get("track_id", [None])[0]
                if raw_id is None:
                    self._error(400, "track_id query param required")
                    return
                try:
                    track_id = int(raw_id)
                except ValueError:
                    self._error(400, "track_id must be an integer")
                    return
                verdict = _get_active_verdict(track_id)
                if verdict is None:
                    self._json(200, {"verdict": None})
                else:
                    self._json(200, {"verdict": verdict})

            elif path == "/analysis":
                raw_id = qs.get("track_id", [None])[0]
                if raw_id is None:
                    self._error(400, "track_id query param required")
                    return
                try:
                    track_id = int(raw_id)
                except ValueError:
                    self._error(400, "track_id must be an integer")
                    return
                self._json(200, {"analysis": _get_analysis(track_id)})

            elif path == "/segments":
                raw_id = qs.get("track_id", [None])[0]
                if raw_id is None:
                    self._error(400, "track_id query param required")
                    return
                try:
                    track_id = int(raw_id)
                except ValueError:
                    self._error(400, "track_id must be an integer")
                    return
                from audio_analysis.segment_analyzer import get_segments

                self._json(200, {"segments": get_segments(DB_PATH, track_id)})

            elif path == "/artwork/generations":
                raw_id = qs.get("track_id", [None])[0]
                if raw_id is None:
                    self._error(400, "track_id query param required")
                    return
                try:
                    track_id = int(raw_id)
                except ValueError:
                    self._error(400, "track_id must be an integer")
                    return
                from artwork.maren_orchestrator import get_generations

                self._json(200, {"generations": get_generations(DB_PATH, track_id)})

            elif path == "/artwork/image":
                raw_id = qs.get("generation_id", [None])[0]
                if raw_id is None:
                    self._error(400, "generation_id query param required")
                    return
                try:
                    generation_id = int(raw_id)
                except ValueError:
                    self._error(400, "generation_id must be an integer")
                    return
                with _db_conn() as conn:
                    row = conn.execute(
                        "SELECT image_url FROM artwork_generations WHERE id = ?",
                        (generation_id,),
                    ).fetchone()
                if row is None or not row["image_url"]:
                    self._error(404, f"Generation {generation_id} has no image")
                    return
                image_path = Path(row["image_url"])
                if not image_path.exists() or not image_path.is_file():
                    self._error(404, f"Image file missing: {image_path}")
                    return
                content_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
                body = image_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "max-age=3600")
                for k, v in CORS_HEADERS.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)
                return

            elif path == "/wave_vault":
                with _db_conn() as conn:
                    rows = conn.execute(
                        """SELECT wv.id, wv.track_id, wv.stem, wv.start_sec, wv.end_sec,
                                  wv.bpm, wv.musical_key, wv.tags, wv.notes,
                                  wv.added_by, wv.added_at,
                                  t.title AS track_title
                             FROM wave_vault wv
                             LEFT JOIN tracks t ON t.id = wv.track_id
                            ORDER BY wv.added_at DESC"""
                    ).fetchall()
                entries = []
                for r in rows:
                    item = dict(r)
                    if item.get("tags"):
                        try:
                            item["tags"] = json.loads(item["tags"])
                        except (TypeError, json.JSONDecodeError):
                            item["tags"] = []
                    else:
                        item["tags"] = []
                    entries.append(item)
                self._json(200, {"entries": entries})

            elif path == "/audio_features":
                raw_id = qs.get("track_id", [None])[0]
                if raw_id is None:
                    self._error(400, "track_id query param required")
                    return
                try:
                    track_id = int(raw_id)
                except ValueError:
                    self._error(400, "track_id must be an integer")
                    return
                from audio_analysis.feature_extractor import get_audio_features

                features = get_audio_features(DB_PATH, track_id)
                if features is None:
                    self._error(404, f"No audio features for track {track_id}")
                    return
                self._json(200, {"features": features})

            elif path == "/insights/graph":
                self._json(200, _build_insights_graph())

            elif path == "/settings":
                self._json(200, _read_settings())

            elif path.startswith("/api/intake/status/"):
                raw_id = path[len("/api/intake/status/") :].strip("/")
                try:
                    pid = int(raw_id)
                except ValueError:
                    self._error(400, "project_id must be an integer")
                    return
                status = _get_intake_status(pid)
                if status is None:
                    self._error(404, f"Project {pid} not found")
                    return
                self._json(200, status)

            else:
                # No API route matched — serve static file (no auth required)
                self._serve_static(path)
                return

        except sqlite3.Error as exc:
            logger.exception("DB error on GET %s", path)
            self._error(500, f"Database error: {exc}")
        except Exception as exc:
            logger.exception("Unexpected error on GET %s", path)
            self._error(500, str(exc))

    # ── POST ────────────────────────────────────────────────────────────────

    def do_POST(self) -> None:
        path, _ = self._parse_url()

        if not self._authenticated():
            self._error(401, "Unauthorized")
            return

        # /api/intake handles its own body parsing (may be multipart OR JSON)
        if path == "/api/intake":
            self._handle_intake()
            return

        # /stt handles its own body parsing (raw multipart audio upload)
        if path == "/stt":
            self._handle_stt()
            return

        try:
            raw = self._read_body()
            if not raw:
                self._error(400, "Empty request body")
                return
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                self._error(400, f"Invalid JSON: {exc}")
                return

            if path == "/event":
                event = data.get("event")
                payload = data.get("payload", {})

                if not event:
                    self._error(400, "'event' field required")
                    return
                if not isinstance(payload, dict):
                    self._error(400, "'payload' must be an object")
                    return
                if "file_path" not in payload:
                    self._error(400, "'payload.file_path' required")
                    return
                if "version" not in payload:
                    self._error(400, "'payload.version' required")
                    return
                try:
                    payload["version"] = int(payload["version"])
                except (TypeError, ValueError):
                    self._error(400, "'payload.version' must be an integer")
                    return

                try:
                    _fire_event(event, payload)
                    self._json(200, {"status": "ok"})
                except Exception as exc:
                    logger.exception("Event handler error for event=%s", event)
                    self._json(500, {"status": "error", "detail": str(exc)})

            elif path == "/settings":
                if not isinstance(data, dict):
                    self._error(400, "Request body must be a JSON object")
                    return
                _write_settings(data)
                self._json(200, {"status": "ok"})

            elif path == "/artist_message":
                if not isinstance(data, dict):
                    self._error(400, "Request body must be a JSON object")
                    return
                agent = data.get("agent")
                message = data.get("message")
                raw_track_id = data.get("track_id")
                raw_timestamp = data.get("timestamp_sec")
                if not isinstance(agent, str):
                    self._error(400, "'agent' required")
                    return
                if not isinstance(message, str):
                    self._error(400, "'message' required")
                    return
                try:
                    track_id = int(raw_track_id) if raw_track_id is not None else None
                    timestamp_sec = (
                        float(raw_timestamp) if raw_timestamp is not None else None
                    )
                    logged = _log_artist_message(
                        agent=agent,
                        message=message,
                        track_id=track_id,
                        timestamp_sec=timestamp_sec,
                    )
                except ValueError as exc:
                    self._error(400, str(exc))
                    return
                self._json(200, logged)

            elif path == "/roundtable/debate":
                if not isinstance(data, dict):
                    self._error(400, "Request body must be a JSON object")
                    return
                raw_track_id = data.get("track_id")
                try:
                    track_id = int(raw_track_id) if raw_track_id is not None else None
                except (TypeError, ValueError):
                    self._error(400, "'track_id' must be an integer")
                    return
                if track_id is None:
                    self._error(400, "'track_id' required")
                    return
                with _db_conn() as conn:
                    exists = conn.execute(
                        "SELECT 1 FROM tracks WHERE id = ?", (track_id,)
                    ).fetchone()
                if exists is None:
                    self._error(404, f"Track {track_id} not found")
                    return
                self._json(200, _kick_debate(track_id))

            elif path == "/release_states":
                if not isinstance(data, dict):
                    self._error(400, "Request body must be a JSON object")
                    return
                try:
                    track_id = int(data.get("track_id"))
                except (TypeError, ValueError):
                    self._error(400, "'track_id' must be an integer")
                    return
                to_state = data.get("to_state")
                if not isinstance(to_state, str):
                    self._error(400, "'to_state' required")
                    return
                changed_by = data.get("changed_by", "artist")
                if not isinstance(changed_by, str):
                    self._error(400, "'changed_by' must be a string")
                    return
                reason = data.get("reason")
                if reason is not None and not isinstance(reason, str):
                    self._error(400, "'reason' must be a string")
                    return
                try:
                    if to_state.upper() == "APPROVED":
                        from coordination.dispatcher import (
                            PipelineError,
                            TrackPipelineDispatcher,
                        )

                        TrackPipelineDispatcher(DB_PATH).process_event(
                            "track_approved",
                            {"track_id": track_id, "agent": changed_by},
                        )
                        with _db_conn() as conn:
                            updated = _track_payload(conn, track_id)
                    else:
                        updated = _transition_track_state(
                            track_id=track_id,
                            to_state=to_state,
                            changed_by=changed_by,
                            reason=reason,
                        )
                except (ValueError, PipelineError) as exc:
                    self._error(400, str(exc))
                    return
                self._json(200, updated)

            elif path == "/tracks/vault":
                if not isinstance(data, dict):
                    self._error(400, "Request body must be a JSON object")
                    return
                try:
                    track_id = int(data.get("track_id"))
                except (TypeError, ValueError):
                    self._error(400, "'track_id' must be an integer")
                    return
                reason = data.get("reason")
                if reason is not None and not isinstance(reason, str):
                    self._error(400, "'reason' must be a string")
                    return
                try:
                    self._json(200, _vault_track(track_id=track_id, reason=reason))
                except ValueError as exc:
                    self._error(400, str(exc))
                    return

            elif path == "/tracks/delete":
                if not isinstance(data, dict):
                    self._error(400, "Request body must be a JSON object")
                    return
                try:
                    track_id = int(data.get("track_id"))
                except (TypeError, ValueError):
                    self._error(400, "'track_id' must be an integer")
                    return
                delete_file = data.get("delete_file", False)
                if not isinstance(delete_file, bool):
                    self._error(400, "'delete_file' must be a boolean")
                    return
                try:
                    self._json(
                        200, _delete_track_tracking(track_id=track_id, delete_file=delete_file)
                    )
                except (OSError, ValueError) as exc:
                    self._error(400, str(exc))
                    return

            elif path == "/verdict/synthesize":
                if not isinstance(data, dict):
                    self._error(400, "Request body must be a JSON object")
                    return
                try:
                    track_id = int(data.get("track_id"))
                except (TypeError, ValueError):
                    self._error(400, "'track_id' must be an integer")
                    return
                try:
                    verdict = _synthesize_verdict(track_id)
                except ValueError as exc:
                    self._error(400, str(exc))
                    return
                self._json(200, {"verdict": verdict})

            elif path == "/verdict/act":
                if not isinstance(data, dict):
                    self._error(400, "Request body must be a JSON object")
                    return
                try:
                    track_id = int(data.get("track_id"))
                    verdict_id = int(data.get("verdict_id"))
                except (TypeError, ValueError):
                    self._error(400, "'track_id' and 'verdict_id' must be integers")
                    return
                try:
                    result = _act_on_verdict(track_id, verdict_id)
                except ValueError as exc:
                    self._error(400, str(exc))
                    return
                self._json(200, result)

            elif path == "/artwork/generate":
                if not isinstance(data, dict):
                    self._error(400, "Request body must be a JSON object")
                    return
                try:
                    track_id = int(data.get("track_id"))
                except (TypeError, ValueError):
                    self._error(400, "'track_id' must be an integer")
                    return
                import asyncio

                from artwork.maren_orchestrator import (
                    MarenOrchestrationError,
                    generate_artwork_variants,
                )

                try:
                    rows = asyncio.run(generate_artwork_variants(DB_PATH, track_id))
                except MarenOrchestrationError as exc:
                    self._error(400, str(exc))
                    return
                self._json(200, {"generations": rows})

            elif path == "/artwork/pick":
                if not isinstance(data, dict):
                    self._error(400, "Request body must be a JSON object")
                    return
                try:
                    generation_id = int(data.get("generation_id"))
                except (TypeError, ValueError):
                    self._error(400, "'generation_id' must be an integer")
                    return
                from artwork.maren_orchestrator import (
                    MarenOrchestrationError,
                    pick_generation,
                )

                try:
                    picked = pick_generation(DB_PATH, generation_id)
                except MarenOrchestrationError as exc:
                    self._error(400, str(exc))
                    return
                self._json(200, {"generation": picked})

            else:
                self._error(404, f"Not found: {path}")

        except Exception as exc:
            logger.exception("Unexpected error on POST %s", path)
            self._error(500, str(exc))


# ── Threading HTTP server ──────────────────────────────────────────────────────


class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """Handle each request in a separate thread."""

    daemon_threads = True  # threads die when the main process exits


# ── Timeout scanner ───────────────────────────────────────────────────────────

# In-memory retry tracker for pipeline errors.  Resets on server restart, which is
# intentional: a fresh start gives tracks one more clean shot at analysis.
_pipeline_retry_counts: dict[int, int] = {}
_MAX_PIPELINE_RETRIES = 3
# Don't retry a pipeline_error that just appeared — wait at least this long.
_PIPELINE_ERROR_MIN_AGE = timedelta(minutes=15)


def _timeout_scanner_loop(interval_seconds: float = 1800.0) -> None:
    """Background thread: scan for stalled tracks and fire timeout events.

    Runs every *interval_seconds* (default 30 min).  For each TIMEOUT_RULE,
    finds tracks that have been in the rule's state longer than max_duration
    and fires the corresponding event through _fire_event so dispatcher.py can
    generate the nag message in the right agent's voice.
    """
    from coordination.state_machine import TIMEOUT_RULES

    while True:
        time.sleep(interval_seconds)
        try:
            with _db_conn() as conn:
                for rule in TIMEOUT_RULES:
                    cutoff = datetime.now(UTC).replace(tzinfo=None) - rule.max_duration
                    # Find the most recent release_states row per track that
                    # transitioned *into* the current state, then check age.
                    rows = conn.execute(
                        """
                        SELECT t.id AS track_id
                        FROM tracks t
                        JOIN (
                            SELECT track_id, MAX(id) AS max_id
                            FROM release_states
                            WHERE to_state = ?
                            GROUP BY track_id
                        ) latest ON latest.track_id = t.id
                        JOIN release_states rs ON rs.id = latest.max_id
                        WHERE t.state = ?
                          AND rs.created_at <= ?
                        """,
                        (rule.state.value, rule.state.value, cutoff.isoformat()),
                    ).fetchall()
                    for row in rows:
                        try:
                            _fire_event(
                                rule.timeout_event,
                                {"track_id": int(row["track_id"]), "state": rule.state.value},
                            )
                            logger.info(
                                "Timeout scanner: fired %s for track %d",
                                rule.timeout_event,
                                int(row["track_id"]),
                            )
                        except Exception:
                            logger.exception(
                                "Timeout scanner: failed to fire %s for track %d",
                                rule.timeout_event,
                                int(row["track_id"]),
                            )
                # Retry tracks stuck in IN_REVIEW with a pipeline_error feedback row.
                # The error row must be old enough that a concurrent run isn't still in progress.
                retry_cutoff = datetime.now(UTC).replace(tzinfo=None) - _PIPELINE_ERROR_MIN_AGE
                stuck = conn.execute(
                    """
                    SELECT DISTINCT t.id AS track_id
                    FROM tracks t
                    JOIN feedback f ON f.track_id = t.id
                    WHERE t.state = 'IN_REVIEW'
                      AND f.intent = 'pipeline_error'
                      AND f.created_at <= ?
                    """,
                    (retry_cutoff.isoformat(),),
                ).fetchall()
                for row in stuck:
                    tid = int(row["track_id"])
                    retries = _pipeline_retry_counts.get(tid, 0)
                    if retries >= _MAX_PIPELINE_RETRIES:
                        logger.warning(
                            "Timeout scanner: track %d has hit max pipeline retries "
                            "(%d), giving up",
                            tid,
                            _MAX_PIPELINE_RETRIES,
                        )
                        continue
                    _pipeline_retry_counts[tid] = retries + 1
                    try:
                        _fire_event("new_track_detected", {"track_id": tid})
                        logger.info(
                            "Timeout scanner: pipeline retry %d/%d for track %d",
                            retries + 1,
                            _MAX_PIPELINE_RETRIES,
                            tid,
                        )
                    except Exception:
                        logger.exception(
                            "Timeout scanner: failed to fire pipeline retry for track %d", tid
                        )
        except Exception:
            logger.exception("Timeout scanner: DB scan error")


# ── Entry point ────────────────────────────────────────────────────────────────


FISH_SPEECH_DIR = Path(os.environ.get("FISH_SPEECH_DIR", r"D:\jj-studio-v2\fish-speech"))
FISH_LOCAL_PORT = int(os.environ.get("FISH_LOCAL_PORT", "8090"))
_FISH_GPU_MIN_VRAM_MB = int(os.environ.get("FISH_PREWARM_MIN_VRAM_MB", "8192"))


def _gpu_total_vram_mb() -> int | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in out.stdout.splitlines():
            line = line.strip().split(",")[0].strip()
            if line.isdigit():
                return int(line)
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def _pick_fish_checkpoint() -> Path | None:
    checkpoints = FISH_SPEECH_DIR / "checkpoints"
    if not checkpoints.is_dir():
        return None
    candidates = sorted(
        (d for d in checkpoints.iterdir() if d.is_dir()),
        key=lambda d: (0 if "s2" in d.name.lower() else 1, d.name),
    )
    for d in candidates:
        if (d / "model.pth").exists() or (d / "config.json").exists():
            return d
    return None


def _prewarm_fish_local() -> None:
    """Auto-start the self-hosted fish-speech API server when a good GPU exists.

    Opt-in: set FISH_PREWARM=1 in the environment/.env to enable. The local
    server currently fails at warm-up with the s1-mini checkpoint (see
    D:\\jj-studio-v2\\fish-speech\\api-server.log), so this stays off by default.
    """
    env_path = Path(__file__).resolve().parent / ".env"
    prewarm_flag = os.environ.get("FISH_PREWARM", "")
    if not prewarm_flag and env_path.exists():
        with suppress(OSError):
            for _line in env_path.read_text(encoding="utf-8").splitlines():
                if _line.strip().startswith("FISH_PREWARM="):
                    prewarm_flag = _line.split("=", 1)[1].strip()
                    break
    if prewarm_flag != "1":
        return

    import sys as _sys

    from audio_analysis.tts import FISH_LOCAL_BASE_URL, local_server_ready

    if local_server_ready(timeout=0.5):
        logger.info("Fish local server already running at %s", FISH_LOCAL_BASE_URL)
        return
    vram = _gpu_total_vram_mb()
    if vram is None or vram < _FISH_GPU_MIN_VRAM_MB:
        logger.info("Fish local prewarm skipped (no NVIDIA GPU with >=%s MB VRAM)", _FISH_GPU_MIN_VRAM_MB)
        return
    ckpt = _pick_fish_checkpoint()
    if ckpt is None:
        logger.info("Fish local prewarm skipped (no checkpoint under %s)", FISH_SPEECH_DIR / "checkpoints")
        return
    venv_python = FISH_SPEECH_DIR / ".venv" / ("Scripts" if os.name == "nt" else "bin") / "python.exe"
    python_exe = venv_python if venv_python.exists() else Path(_sys.executable)
    cmd = [
        str(python_exe),
        "-m",
        "tools.api_server",
        "--listen",
        f"127.0.0.1:{FISH_LOCAL_PORT}",
        "--llama-checkpoint-path",
        str(ckpt),
        "--decoder-checkpoint-path",
        str(ckpt / "codec.pth"),
        "--decoder-config-name",
        "modded_dac_vq",
        "--half",
    ]
    log_file = open(FISH_SPEECH_DIR / "api-server.log", "ab")  # noqa: SIM115 - lifetime of the process
    try:
        subprocess.Popen(
            cmd,
            cwd=str(FISH_SPEECH_DIR),
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        logger.warning("Fish local prewarm failed to launch: %s", exc)
        return
    logger.info("Fish local prewarm launched (ckpt=%s, vram=%s MB)", ckpt.name, vram)


def main() -> None:
    # Render.com sets PORT automatically; locally we use API_PORT (default 8086)
    port = int(os.environ.get("PORT") or os.environ.get("API_PORT", "8086"))
    _load_or_create_token()
    conductor_tick = float(os.environ.get("STUDIO_CONDUCTOR_TICK_SECONDS", "30"))

    logger.info("Data dir  : %s", _DATA_DIR)
    logger.info("DB path   : %s", DB_PATH)
    logger.info(
        "Static dir: %s%s", STATIC_DIR, " (exists)" if STATIC_DIR.is_dir() else " (not built)"
    )
    logger.info("Token file: %s", _TOKEN_FILE)
    logger.info("API token : <redacted>")
    logger.info("Starting HTTP API on port %d", port)

    from coordination.conductor_runtime import StudioConductorRuntime

    conductor_runtime = StudioConductorRuntime(
        DB_PATH, tick_seconds=conductor_tick, pipeline_lock=_pipeline_lock
    )
    conductor_runtime.start()

    # Nag / timeout scanner — fires timeout_feedback_stale, timeout_art_overdue,
    # timeout_release_date_missed when tracks stall.  Runs every 30 min by default.
    timeout_interval = float(os.environ.get("TIMEOUT_SCAN_INTERVAL_SECONDS", "1800"))
    timeout_thread = threading.Thread(
        target=_timeout_scanner_loop,
        args=(timeout_interval,),
        daemon=True,
        name="timeout-scanner",
    )
    timeout_thread.start()
    logger.info("Timeout scanner started (interval=%.0fs)", timeout_interval)

    prewarm_thread = threading.Thread(target=_prewarm_fish_local, daemon=True, name="fish-prewarm")
    prewarm_thread.start()

    server = ThreadedHTTPServer(("0.0.0.0", port), RecordLabelHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down HTTP API server")
    finally:
        conductor_runtime.stop()
        server.server_close()


if __name__ == "__main__":
    main()
