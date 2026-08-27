"""Live coordination dispatcher for watcher/API events.

This is the concrete bridge between file intake and the release pipeline.  The
rules package defines what should happen; this module performs the database
updates and analysis work for the early track-review path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import sqlite3
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx
from artwork.maren_orchestrator import (
    MarenOrchestrationError,
    generate_artwork_variants,
)
from audio_analysis.analyzer import AnalyzerError, analyze
from audio_analysis.embedding_extractor import (
    EmbeddingExtractionError,
    extract_embedding,
)
from audio_analysis.feature_extractor import (
    FeatureExtractionError,
    extract_audio_features,
)
from audio_analysis.gemini_client import DEFAULT_OPENROUTER_MODEL, _openrouter_key
from audio_analysis.models import AudioAnalysis
from audio_analysis.segment_analyzer import (
    SegmentAnalysisError,
    analyze_segments,
)
from stem_separation.separator import STEM_NAMES, StemSeparatorError, separate_stems

from coordination.intent_parser import IntentParser, IntentType

logger = logging.getLogger(__name__)


REPO_ROOT = Path(__file__).resolve().parents[1]
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_AGENT_MODEL = "qwen/qwen3.8-27b"
AGENT_SOUL_PATHS = {
    "a_and_r": REPO_ROOT / "agents" / "a_and_r" / "SOUL.md",
    "kallman": REPO_ROOT / "agents" / "kallman" / "SOUL.md",
    "janick": REPO_ROOT / "agents" / "janick" / "SOUL.md",
    "rhone": REPO_ROOT / "agents" / "rhone" / "SOUL.md",
    "rubin": REPO_ROOT / "agents" / "rubin" / "SOUL.md",
    "manager": REPO_ROOT / "agents" / "manager" / "SOUL.md",
    "creative_director": REPO_ROOT / "agents" / "creative_director" / "SOUL.md",
}
AGENT_RESEARCH_PATHS = {
    "kallman": REPO_ROOT / "research" / "label-execs" / "craig_kallman.yaml",
    "janick": REPO_ROOT / "research" / "label-execs" / "john_janick.yaml",
    "rhone": REPO_ROOT / "research" / "label-execs" / "sylvia_rhone.yaml",
    "rubin": REPO_ROOT / "research" / "label-execs" / "rick_rubin.yaml",
}
ROUNDTABLE_AGENTS = [
    "kallman",
    "a_and_r",
    "janick",
    "rhone",
    "rubin",
    "creative_director",
    "manager",
]
# Music-side execs who always give a first read; Maren (creative) and the
# release desk (bandcamp) join the room session when their lane is needed.
MUSIC_EXECS = ["kallman", "a_and_r", "janick", "rhone", "rubin"]
VISUAL_CONCEPTION_TASKS = {
    "kallman": "Give one snap visual image that matches your instinct about whether the record declares itself.",
    "a_and_r": "Add one compact visual image grounded in a real musical moment, lyric, or texture.",
    "janick": "Describe one image that could establish the larger world or era around this song.",
    "rhone": "Offer one visual image rooted in the song's cultural and emotional specificity.",
    "rubin": "Offer one spare image that expresses the song's essential truth without decoration.",
    "manager": "Include one practical visual frame that makes the room's direction easy to picture.",
    "creative_director": (
        "Lead with the room's most vivid visual conception. Translate the song into a specific scene, "
        "palette, light, texture, composition, and camera behavior or motion. Connect those choices to "
        "the lyrics or musical ideas actually present, then ask the artist one generative question that "
        "invites them to shape the world with you. Stay concise, but make the image palpable."
    ),
}
AGENT_DISPLAY_NAMES = {
    "kallman": "Craig Kallman",
    "a_and_r": "Ravi Kendrick",
    "janick": "John Janick",
    "rhone": "Sylvia Rhone",
    "rubin": "Rick Rubin",
    "creative_director": "Maren",
    "manager": "Dez Montoya",
}
AGENT_LENSES = {
    "kallman": "commercial conviction - does this declare itself instantly, is it a hit, would he run it back",
    "a_and_r": "mix and production craft - low end, frequency balance, arrangement problems, exact timestamps",
    "janick": "artist identity and world-building - eras, catalog through-lines, what body of work this starts",
    "rhone": "culture and audience - where it comes from, who claims it first, authenticity vs engineered crossover",
    "rubin": "the essence - the one true thing the song says, what to strip away, what must stay untouched",
    "creative_director": "the visual world - palette, light, scene, texture, camera motion translated from the music",
    "manager": "decisions and logistics only - gates, owners, next moves; closes rounds when a decision is needed, never chats",
}
ROUND_TABLE_INTENTS = {
    "kallman": "early_conviction_feedback",
    "a_and_r": "analysis_feedback",
    "janick": "vision_assessment",
    "rhone": "cultural_authenticity_read",
    "rubin": "essential_question_review",
    "creative_director": "visual_conception",
    "manager": "review_round_summary",
}
ROUND_MAX_TURNS = len(ROUNDTABLE_AGENTS)
AGENT_ADDRESS_ALIASES = {
    "creative_director": ("creative director", "maren"),
    "a_and_r": ("a&r", "a and r", "ravi"),
    "manager": ("manager", "dez"),
    "kallman": ("kallman", "craig"),
    "janick": ("janick", "john"),
    "rhone": ("rhone", "sylvia"),
    "rubin": ("rubin", "rick"),
}
AGENT_TASKS = {
    "a_and_r": (
        "You are writing Ravi's real first-pass A&R note after listening to the track. "
        "Be concrete, musical, and useful. Use timestamps only when the supplied analysis supports them. "
        "Name what is working, what is not landing yet, and the single most useful next move. "
        "Keep it to 2-4 short sentences in Ravi's lowercase style."
    ),
    "kallman": (
        "You are writing Craig Kallman's early-conviction read. "
        "This is a fast gut signal, not a detailed critique. Decide whether the record declares itself, "
        "whether there is real early conviction, and what makes it distinctive or too hedged. "
        "Keep it to 1-2 short lowercase sentences."
    ),
    "janick": (
        "You are writing John Janick's vision read. "
        "Treat the song as one chapter in a larger body of work. Ask whether there is a world, era, or artist identity here, "
        "not whether the track is simply good. Prefer one pointed question or one precise observation. "
        "Keep it minimal: 1-3 lowercase sentences."
    ),
    "rhone": (
        "You are writing Sylvia Rhone's cultural-authenticity read. "
        "Name whether the record feels rooted, compromised, or still forming, and who would claim it first if anyone would. "
        "Speak with warmth and clarity, not lecture. "
        "Keep it to 2-3 lowercase sentences."
    ),
    "rubin": (
        "You are writing Rick Rubin's production-truth note. "
        "Focus on the essential truth of the song, the single thing it is trying to transmit, "
        "and whether anything in the arrangement is obscuring that. Ask or observe; do not prescribe additions. "
        "Keep it to 1-3 sparse lowercase sentences."
    ),
    "manager": (
        "You are writing Dez's room summary for the artist. "
        "Summarize what the team actually decided and state the next action clearly. "
        "Be concrete about the gate and the owner's move. Do not sound like a system log. "
        "Keep it direct and action-oriented in 2-4 short sentences."
    ),
    "creative_director": (
        "You are writing Maren's creative-direction contribution to the roundtable. "
        "Ground the visual direction in the music's mood, texture, lyrics, and identity, and connect it "
        "to catalog continuity when relevant. Be visually literate, specific, and conversational. "
        "Keep it to 2-4 sentences."
    ),
}
AGENT_STYLE_GUIDES = {
    "kallman": (
        "Craig Kallman is a fast, decisive conviction scout. He sends one blunt executive-text take. "
        "He judges inevitability, distinctiveness, replay instinct, and whether the music declares itself quickly. "
        "He does not talk about worlds, eras, structure maps, or detailed critique. "
        "Good outputs sound like: 'yeah this has it' / 'jury's out' / 'not landing yet' / 'send this to ravi'."
    ),
    "janick": (
        "John Janick is quiet and strategic. He is not judging the song's craft. "
        "He is asking whether this artist is building a world, an era, or just collecting songs. "
        "He often sends one question. He does not talk about bpm, arrangement, minimal variation, or energy curves. "
        "Good outputs sound like: 'what are you building?' / 'i see the through-line' / 'what comes after this chapter?'."
    ),
    "rhone": (
        "Sylvia Rhone is warm, direct, culturally grounded. She asks where the music comes from and who will claim it first. "
        "She protects specificity and can spot engineered crossover. She may use 'honey' once, naturally. "
        "She does not sound like an analyzer or a moral lecture."
    ),
    "rubin": (
        "Rick Rubin is sparse, patient, and subtractive. He asks what the song is really trying to say and whether anything is in the way. "
        "He does not give mix notes, technical fixes, or analyzer phrases unless transformed into a deeper question. "
        "Good outputs sound like: 'what is this trying to say?' / 'what if it's less?' / 'what is the truest part here?'."
    ),
    "manager": (
        "Dez is an actual manager, not a dashboard summary. He names the decision, the blocker, and the owner's next move. "
        "He is plainspoken, concrete, and operational."
    ),
    "creative_director": (
        "Maren is visually literate and specific. They translate music into palette, texture, composition, and catalog continuity. "
        "They do not give generic mood-board filler."
    ),
    "a_and_r": (
        "Ravi is the only one who should comfortably sound musical and analytic. "
        "He names specific moments, elements, and directional fixes in lowercase conversational language."
    ),
}


class PipelineError(Exception):
    """Raised when a pipeline event cannot be processed."""


def _load_dotenv() -> None:
    """Load repo .env values into the process without printing secrets."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _db_conn(db_path: str) -> Iterator[sqlite3.Connection]:
    conn = _connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


class AgentVoiceError(Exception):
    """Raised when agent voice generation fails."""


@lru_cache(maxsize=None)
def _load_agent_soul(agent: str) -> str:
    path = AGENT_SOUL_PATHS.get(agent)
    if path is None or not path.exists():
        raise AgentVoiceError(f"SOUL.md not found for agent {agent}")
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def _load_agent_research(agent: str) -> str:
    path = AGENT_RESEARCH_PATHS.get(agent)
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _insert_feedback(
    conn: sqlite3.Connection,
    *,
    track_id: int,
    project_id: int | None = None,
    agent: str,
    message: str,
    intent: str,
    channel: str = "desktop",
    timestamp_sec: float | None = None,
) -> None:
    exists = conn.execute(
        "SELECT 1 FROM feedback WHERE track_id = ? AND agent = ? AND intent = ? LIMIT 1",
        (track_id, agent, intent),
    ).fetchone()
    if exists:
        return
    conn.execute(
        """INSERT INTO feedback
            (track_id, project_id, agent, message, channel, direction, intent, timestamp_sec)
            VALUES (?, ?, ?, ?, ?, 'outbound', ?, ?)""",
        (track_id, project_id, agent, message, channel, intent, timestamp_sec),
    )


def _upsert_feedback_message(
    conn: sqlite3.Connection,
    *,
    track_id: int | None,
    project_id: int | None,
    agent: str,
    message: str,
    channel: str,
    direction: str,
    intent: str | None,
    timestamp_sec: float | None = None,
) -> int:
    message = message.strip()
    if not message:
        raise PipelineError("Feedback message cannot be empty")

    if track_id is None:
        row = conn.execute(
            """SELECT id FROM feedback
               WHERE track_id IS NULL AND agent = ? AND message = ? AND direction = ?
               ORDER BY id DESC
               LIMIT 1""",
            (agent, message, direction),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT id FROM feedback
               WHERE track_id = ? AND agent = ? AND message = ? AND direction = ?
               ORDER BY id DESC
               LIMIT 1""",
            (track_id, agent, message, direction),
        ).fetchone()

    if row is not None:
        feedback_id = int(row["id"])
        conn.execute(
            """UPDATE feedback
               SET project_id = COALESCE(?, project_id),
                   channel = ?,
                   intent = COALESCE(?, intent),
                   timestamp_sec = COALESCE(?, timestamp_sec)
               WHERE id = ?""",
            (project_id, channel, intent, timestamp_sec, feedback_id),
        )
        return feedback_id

    cur = conn.execute(
        """INSERT INTO feedback
           (track_id, project_id, agent, message, channel, direction, intent, timestamp_sec)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (track_id, project_id, agent, message, channel, direction, intent, timestamp_sec),
    )
    if cur.lastrowid is None:
        raise RuntimeError("INSERT into feedback did not return a lastrowid")
    return cur.lastrowid


def _submit_pending_message(
    conn: sqlite3.Connection,
    *,
    track_id: int | None,
    agent: str,
    draft: str,
    context: str,
    priority: str = "normal",
) -> None:
    if not _table_exists(conn, "pending_messages"):
        return
    if track_id is None:
        exists = conn.execute(
            """SELECT 1 FROM pending_messages
               WHERE track_id IS NULL AND from_agent = ? AND context = ?
               LIMIT 1""",
            (agent, context),
        ).fetchone()
    else:
        exists = conn.execute(
            """SELECT 1 FROM pending_messages
               WHERE track_id = ? AND from_agent = ? AND context = ?
               LIMIT 1""",
            (track_id, agent, context),
        ).fetchone()
    if exists:
        return
    conn.execute(
        """INSERT INTO pending_messages
           (from_agent, draft, context, track_id, priority)
           VALUES (?, ?, ?, ?, ?)""",
        (agent, draft, context, track_id, priority),
    )


def _transition(
    conn: sqlite3.Connection,
    *,
    track_id: int,
    from_state: str,
    to_state: str,
    changed_by: str,
    reason: str,
) -> None:
    if from_state == to_state:
        return
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


def _latest_analysis_id(conn: sqlite3.Connection, track_id: int) -> int | None:
    row = conn.execute(
        """SELECT id FROM audio_analyses
           WHERE track_id = ?
           ORDER BY created_at DESC, id DESC
           LIMIT 1""",
        (track_id,),
    ).fetchone()
    return int(row["id"]) if row else None


def _decode_analysis_json(row: sqlite3.Row, column: str, default: Any) -> Any:
    raw = row[column]
    if raw in (None, ""):
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PipelineError(f"Stored audio analysis has invalid {column} JSON") from exc


def _latest_analysis(conn: sqlite3.Connection, track_id: int) -> AudioAnalysis | None:
    row = conn.execute(
        """SELECT * FROM audio_analyses
           WHERE track_id = ?
           ORDER BY created_at DESC, id DESC
           LIMIT 1""",
        (track_id,),
    ).fetchone()
    if row is None:
        return None
    return AudioAnalysis(
        track_id=track_id,
        model_used=str(row["model_used"]),
        bpm=row["bpm"],
        musical_key=row["musical_key"],
        energy_curve=_decode_analysis_json(row, "energy_curve", []),
        structure=_decode_analysis_json(row, "structure", {}),
        instruments=_decode_analysis_json(row, "instruments", []),
        genre_tags=_decode_analysis_json(row, "genre_tags", []),
        mood_tags=_decode_analysis_json(row, "mood_tags", []),
        mix_observations=_decode_analysis_json(row, "mix_observations", []),
        notable_moments=_decode_analysis_json(row, "notable_moments", []),
        raw_response=row["raw_response"],
    )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'virtual table') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _normalize_feedback_intent(intent: str | None) -> str | None:
    if intent is None:
        return None
    normalized = str(intent).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "approve": "approval",
        "approved": "approval",
        "artist_approves": "approval",
        "approval": "approval",
        "ship_it": "approval",
        "reject": "rejection",
        "rejected": "rejection",
        "rejection": "rejection",
        "revise": "revision",
        "revised": "revision",
        "revision": "revision",
        "revision_uploaded": "revision",
        "redo": "revision",
        "delay": "delay",
        "question": "question",
        "casual": "casual",
    }
    return aliases.get(normalized, normalized or None)


def _named_response_agents(message: str) -> list[str]:
    lowered = message.casefold()
    return [
        agent
        for agent in ROUNDTABLE_AGENTS
        if any(alias in lowered for alias in AGENT_ADDRESS_ALIASES.get(agent, ()))
    ]


def _latest_artist_named_agents(
    conn: sqlite3.Connection, track_id: int, *, limit: int = 8
) -> list[str]:
    rows = conn.execute(
        """SELECT message FROM feedback
           WHERE track_id = ? AND direction = 'inbound'
           ORDER BY id DESC LIMIT ?""",
        (track_id, limit),
    ).fetchall()
    for row in rows:
        agents = _named_response_agents(str(row["message"]))
        if agents:
            return agents
    return []


def _addressed_response_agents(message: str, prior_agents: list[str]) -> list[str]:
    named = _named_response_agents(message)
    if named:
        return named
    lowered = message.casefold()
    pronoun_followup = any(
        phrase in lowered
        for phrase in (
            "what does she",
            "what did she",
            "hear from her",
            "let her",
            "her take",
            "her idea",
        )
    )
    return prior_agents if pronoun_followup else []


def _intent_from_pending_context(context: str | None) -> str | None:
    if context is None:
        return None
    normalized = _normalize_feedback_intent(context)
    if normalized is not None:
        return normalized
    cleaned = str(context).strip().lower().replace("-", "_").replace(" ", "_")
    return cleaned or None


def _track_title(track: sqlite3.Row) -> str:
    title = track["title"] if "title" in tuple(track.keys()) else None
    if title:
        return str(title)
    return Path(str(track["file_path"])).stem


def _safe_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True)


def _audio_memory_snapshot(conn: sqlite3.Connection, *, limit: int = 8) -> list[dict[str, Any]]:
    if not _table_exists(conn, "audio_memory"):
        return []
    rows = conn.execute(
        """SELECT category, observation, confidence, times_observed
           FROM audio_memory
           ORDER BY confidence DESC, last_updated DESC, id DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [
        {
            "category": str(row["category"]),
            "observation": str(row["observation"]),
            "confidence": float(row["confidence"]),
            "times_observed": int(row["times_observed"]),
        }
        for row in rows
    ]


def _catalog_tracks_snapshot(
    conn: sqlite3.Connection,
    *,
    project_id: int | None,
    track_id: int,
    limit: int = 6,
) -> list[dict[str, Any]]:
    if project_id is not None and _table_exists(conn, "tracks"):
        rows = conn.execute(
            """SELECT id, title, state, version, updated_at
               FROM tracks
               WHERE project_id = ?
               ORDER BY updated_at DESC, id DESC
               LIMIT ?""",
            (project_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, title, state, version, updated_at
               FROM tracks
               ORDER BY updated_at DESC, id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "id": int(row["id"]),
                "title": str(row["title"] or f"track-{row['id']}"),
                "state": str(row["state"]),
                "version": int(row["version"]),
                "updated_at": str(row["updated_at"]),
                "current_track": int(row["id"]) == track_id,
            }
        )
    return result


def _recent_feedback_snapshot(
    conn: sqlite3.Connection,
    *,
    track_id: int,
    limit: int = 10,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT agent, intent, message, direction, created_at
           FROM feedback
           WHERE track_id = ?
           ORDER BY id DESC
           LIMIT ?""",
        (track_id, limit),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in reversed(rows):
        result.append(
            {
                "agent": str(row["agent"]),
                "intent": str(row["intent"] or ""),
                "direction": str(row["direction"]),
                "created_at": str(row["created_at"]),
                "message": str(row["message"]),
            }
        )
    return result


def _analysis_snapshot(analysis: AudioAnalysis | None) -> dict[str, Any]:
    if analysis is None:
        return {}
    return {
        "model_used": analysis.model_used,
        "bpm": analysis.bpm,
        "musical_key": analysis.musical_key,
        "genre_tags": analysis.genre_tags[:6],
        "mood_tags": analysis.mood_tags[:6],
        "instruments": analysis.instruments[:10],
        "structure": analysis.structure,
        "notable_moments": [
            {
                "timestamp": moment.timestamp,
                "description": moment.description,
                "quality_judgment": moment.quality_judgment,
            }
            for moment in analysis.notable_moments[:8]
        ],
        "mix_observations": [
            {"timestamp": note.timestamp, "observation": note.observation}
            for note in analysis.mix_observations[:8]
        ],
    }


def _segments_snapshot(conn: sqlite3.Connection, track_id: int) -> list[dict[str, Any]]:
    """All segments for a track in time order. Empty list = no analysis yet.

    Standout segments are critical evidence the agents should be pointing at,
    so we surface every standout's full reason and visual anchor.
    """
    if not _table_exists(conn, "track_segments"):
        return []
    rows = conn.execute(
        """SELECT start_sec, end_sec, section_label, energy, elements_present,
                  mood, production_notes, standout, standout_reason, visual_anchor
             FROM track_segments
            WHERE track_id = ?
            ORDER BY start_sec ASC""",
        (track_id,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        raw_elements = row["elements_present"]
        if raw_elements:
            elements = json.loads(raw_elements)
            if not isinstance(elements, list):
                raise PipelineError(
                    f"track_segments.elements_present for track {track_id} "
                    f"is not a JSON array: {elements!r}"
                )
        else:
            elements = []
        out.append(
            {
                "start_sec": float(row["start_sec"]),
                "end_sec": float(row["end_sec"]),
                "section_label": row["section_label"],
                "energy": row["energy"],
                "elements_present": elements,
                "mood": row["mood"],
                "production_notes": row["production_notes"],
                "standout": bool(row["standout"]),
                "standout_reason": row["standout_reason"],
                "visual_anchor": row["visual_anchor"],
            }
        )
    return out


def _lyrics_snapshot(conn: sqlite3.Connection, track_id: int) -> dict[str, Any] | None:
    """Cleaned lyrics + vocal style. None when the lyrics row doesn't exist."""
    if not _table_exists(conn, "track_lyrics"):
        return None
    row = conn.execute(
        """SELECT lyrics_clean, vocal_style, vocal_observations, language, explicit
             FROM track_lyrics
            WHERE track_id = ?""",
        (track_id,),
    ).fetchone()
    if row is None:
        return None
    observations: list[str] = []
    if row["vocal_observations"]:
        parsed = json.loads(row["vocal_observations"])
        if not isinstance(parsed, list):
            raise PipelineError(
                f"track_lyrics.vocal_observations for track {track_id} "
                f"is not a JSON array: {parsed!r}"
            )
        observations = [str(item) for item in parsed]
    return {
        "lyrics_clean": row["lyrics_clean"],
        "vocal_style": row["vocal_style"],
        "vocal_observations": observations,
        "language": row["language"],
        "explicit": bool(row["explicit"]) if row["explicit"] is not None else False,
    }


def _essence_snapshot(conn: sqlite3.Connection, track_id: int) -> list[str]:
    """Rubin's essence_elements for a track. Empty only when the instrumental
    analysis hasn't run; malformed stored JSON is treated as data corruption
    and raised — never silently swallowed.
    """
    if not _table_exists(conn, "stem_instrumental_analyses"):
        return []
    row = conn.execute(
        "SELECT essence_elements FROM stem_instrumental_analyses WHERE track_id = ?",
        (track_id,),
    ).fetchone()
    if row is None or not row["essence_elements"]:
        return []
    parsed = json.loads(row["essence_elements"])
    if not isinstance(parsed, list):
        raise PipelineError(
            f"essence_elements for track {track_id} is not a JSON array: {parsed!r}"
        )
    return [str(item) for item in parsed]


def _track_prompt_context(
    conn: sqlite3.Connection,
    *,
    track: sqlite3.Row,
    project_id: int | None,
    analysis: AudioAnalysis | None,
    stage: str,
) -> str:
    project = None
    if project_id is not None and _table_exists(conn, "projects"):
        project = conn.execute(
            "SELECT id, title, type, state, target_track_count FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
    context = {
        "stage": stage,
        "track": {
            "id": int(track["id"]),
            "title": _track_title(track),
            "state": str(track["state"]),
            "version": int(track["version"]),
            "format": str(track["format"] or ""),
            "file_path": str(track["file_path"]),
        },
        "project": (
            {
                "id": int(project["id"]),
                "title": str(project["title"]),
                "type": str(project["type"]),
                "state": str(project["state"]),
                "target_track_count": int(project["target_track_count"]),
            }
            if project is not None
            else None
        ),
        "analysis": _analysis_snapshot(analysis),
        "segments": _segments_snapshot(conn, int(track["id"])),
        "lyrics": _lyrics_snapshot(conn, int(track["id"])),
        "essence_elements": _essence_snapshot(conn, int(track["id"])),
        "audio_memory": _audio_memory_snapshot(conn),
        "catalog_tracks": _catalog_tracks_snapshot(
            conn,
            project_id=project_id,
            track_id=int(track["id"]),
        ),
        "recent_feedback": _recent_feedback_snapshot(conn, track_id=int(track["id"])),
    }
    return _safe_json(context)


async def _generate_agent_message_async(
    *,
    agent: str,
    prompt_context: str,
    model: str,
    api_key: str,
    task_override: str | None = None,
    audience: str = "artist",
    track_duration: float | None = None,
) -> tuple[str, float | None]:
    soul = _load_agent_soul(agent)
    research = _load_agent_research(agent)
    task = task_override or AGENT_TASKS.get(agent)
    if task is None:
        raise AgentVoiceError(f"No agent task prompt configured for {agent}")
    visual_task = VISUAL_CONCEPTION_TASKS.get(agent)
    if visual_task:
        task = f"{task} {visual_task} Treat it as a proposed interpretation, not a settled production decision."
    system_prompt = (
        "You are generating one outbound message for the AI Record Label app.\n"
        "Stay fully in character according to the soul document and any attached professional research profile.\n"
        + (
            "This message is spoken INSIDE THE ROOM: you are talking to your fellow label executives, "
            "not to the artist. Address other agents by name when responding to them. Push back, defend "
            "your take, and argue it out. The artist is listening in silently and wants the real debate; "
            "if the artist's own input is needed on a point, turn and ask them directly.\n"
            if audience == "room"
            else "Write like a real person talking in a live roundtable, not an analyst summary or app caption.\n"
        )
        + "Use only the provided context. Do not invent moments, transitions, motives, or facts.\n"
        "Do not invent deadlines, dates, release targets, named visual references, or approvals that are not explicitly supported by the context.\n"
        "You may propose an original visual interpretation when it is grounded in the supplied music, lyrics, mood, texture, or visual anchors; make clear it is your conception, not a fact.\n"
        "Do not repeat the context blob back in generic terms. Interpret it through the agent's taste and role.\n"
        "Only mention facts like loop-based structure, minimal variation, flat energy, or timestamps when they are truly central to the point the agent would naturally make.\n"
        "If you are not Ravi or Dez, avoid sounding like an analyzer. Turn facts into taste judgments, questions, or direction appropriate to the role.\n"
        "Do not use pipe-separated summaries. Do not sound like a report unless the task explicitly calls for a room summary.\n"
        "Do not mention being an AI, prompt, JSON, or analysis object.\n"
        "\n"
        "EVERY MESSAGE MUST DO THESE FOUR THINGS:\n"
        "1. STANCE: commit to a real opinion. Say what you would do - ship it, fix something specific, protect something specific. Never sit neutral and never hedge both ways.\n"
        "2. EVIDENCE: point at one real thing - a timestamp from segments or notable_moments, a verbatim lyric line, or a concrete sonic detail from the analysis.\n"
        "3. CINEMA: give one vivid flash of what you see when this plays - a place, light, texture, camera move, or moment. One sentence, specific, no stock phrases. It must be YOUR image - never reuse or mirror a visual someone already described this round.\n"
        "4. ACTION: end with exactly one action item for the artist - what to change, what to keep untouched as-is, or what to decide next.\n"
        "\n"
        "ROOM RULES:\n"
        "- Other agents' recent takes are in recent_feedback and any round transcript. NEVER restate a point someone already made. Build on it, name them and counter it directly, or bring an angle nobody has touched.\n"
        "- Every invited agent contributes from their own lane; do not sit out the round.\n"
        "- Disagree openly when you disagree. Two executives wanting opposite things is more useful to the artist than fake consensus.\n"
        "\n"
        "Length: message 40-110 words; visual_conception 10-20 words (Maren may use 15-35). Hard cap 150 words total.\n"
        "\n"
        "CONTEXT FIELDS YOU CAN POINT AT:\n"
        "- analysis: whole-track summary (BPM, key, instruments, mood, mix observations, notable moments)\n"
        "- segments: array of structural segments with start_sec, end_sec, section_label, energy (1-10),\n"
        "  elements_present (which sounds are active here), mood, production_notes, standout (bool),\n"
        "  standout_reason, visual_anchor. When you cite a moment, cite the start_sec from a real segment\n"
        "  — never invent a timestamp. The standout segments are the moments worth pointing at.\n"
        "- lyrics: lyrics_clean (full transcript), vocal_style, vocal_observations. Quote a lyric line\n"
        "  verbatim when the words are doing the work; never paraphrase as if a lyric.\n"
        "- essence_elements: Rubin's 'non-negotiables' for this track — the 1-3 things that define it.\n"
        "  When you talk about what the track IS, anchor it here.\n"
        "- catalog_tracks: the artist's other releases for comparison.\n"
        "- recent_feedback: what other agents already said. Don't repeat them; build on or push back.\n"
        "\n"
        "If a field is empty or null, that data hasn't been produced yet — don't pretend to have it,\n"
        "and don't fabricate to fill the gap. Speak only about what's actually in front of you.\n"
        "\n"
        "Return ONLY valid JSON. Fields: message (your role-specific take, non-empty string), "
        'visual_conception (one concrete image, non-empty string), and timestamp_sec.\n'
        "timestamp_sec: the position in the track, in seconds from 0:00, that your take is about. "
        "Use an exact start_sec from a real segment or a notable_moments moment when your point is "
        "anchored to a specific part of the song. Use null only when your take is about the track "
        "as a whole. Never invent a timestamp that does not exist in the context.\n"
        'Example: {"message": "...", "visual_conception": "...", "timestamp_sec": 74.0}\n'
        "The visual_conception is a proposal grounded in the song, not a claim about an existing video.\n\n"
        f"SOUL DOCUMENT:\n{soul}"
    )
    if research:
        system_prompt += f"\n\nPROFESSIONAL RESEARCH PROFILE:\n{research}"
    style_guide = AGENT_STYLE_GUIDES.get(agent)
    if style_guide:
        system_prompt += f"\n\nDISTILLED STYLE GUIDE:\n{style_guide}"
    user_prompt = (
        f"AGENT: {agent}\n"
        f"TASK: {task}\n"
        "Write the single message this agent should send right now.\n"
        "Keep the style aligned with the soul document and the stage.\n"
        "Ground every claim in the supplied context blob.\n\n"
        f"CONTEXT:\n{prompt_context}"
    )
    request_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.78,
        # These are short voice messages, not reasoning tasks. Some providers
        # otherwise spend the entire completion budget on hidden thinking and
        # return truncated JSON (observed with Gemini 3.5 Flash).
        # Qwen 3.8 defaults to xhigh reasoning on OpenRouter. These short,
        # structured voice turns do not need it; disabling reasoning preserves
        # the completion budget for the actual JSON message.
        "reasoning": {"enabled": False, "exclude": True},
        "max_tokens": 384,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        for attempt in range(2):
            response = await client.post(
                OPENROUTER_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://ai-record-label.local",
                    "X-Title": "AI Record Label Agent Voices",
                },
                json=request_payload,
            )
            if response.is_error:
                raise AgentVoiceError(
                    f"OpenRouter HTTP {response.status_code}: {response.text[:500]}"
                )
            body = response.json()
            try:
                raw_content = body["choices"][0]["message"]["content"]
                if not isinstance(raw_content, str) or not raw_content.strip():
                    raise TypeError("content is empty or null")
                content = raw_content.strip()
                if content.startswith("```"):
                    lines = content.splitlines()
                    if lines and lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    content = "\n".join(lines).strip()
                parsed = json.loads(content)
                raw_message = parsed["message"]
                if not isinstance(raw_message, str) or not raw_message.strip():
                    raise TypeError("message is empty or null")
                raw_visual = parsed["visual_conception"]
                if not isinstance(raw_visual, str) or not raw_visual.strip():
                    raise TypeError("visual_conception is empty or null")
                timestamp_sec = _sanitize_feedback_timestamp(
                    parsed.get("timestamp_sec"), track_duration
                )
                return (
                    f"{raw_message.strip()} Visually: {raw_visual.strip()}",
                    timestamp_sec,
                )
            except (KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
                if attempt == 0:
                    logger.warning("Retrying malformed %s voice response", agent)
                    continue
                snippet = str(body)[:500]
                raise AgentVoiceError(
                    f"Invalid {agent} voice response payload after retry: {snippet}"
                ) from exc
    raise AgentVoiceError(f"{agent} voice generation exhausted retries")


def _sanitize_feedback_timestamp(raw: Any, track_duration: float | None) -> float | None:
    """Coerce a model-provided timestamp into a safe 0..duration seconds value, or None."""
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if not math.isfinite(value) or value < 0:
        return None
    if track_duration is not None and track_duration > 0:
        value = min(value, track_duration)
    return round(value, 3)


def _agent_model() -> str:
    return (
        os.environ.get("OPENROUTER_AGENT_MODEL", DEFAULT_AGENT_MODEL).strip() or DEFAULT_AGENT_MODEL
    )


async def _generate_agent_message_bundle_async(
    *,
    agents: list[str],
    prompt_context: str,
    model: str,
    api_key: str,
    task_overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    tasks = [
        _generate_agent_message_async(
            agent=agent,
            prompt_context=prompt_context,
            model=model,
            api_key=api_key,
            task_override=(task_overrides or {}).get(agent),
        )
        for agent in agents
    ]
    results = await asyncio.gather(*tasks)
    return {
        agent: message
        for agent, (message, _timestamp) in zip(agents, results, strict=True)
    }


def _generate_agent_message_bundle(
    *,
    agents: list[str],
    prompt_context: str,
    task_overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    api_key = _openrouter_key(os.environ.get("OPENROUTER_API_KEY"))
    return asyncio.run(
        _generate_agent_message_bundle_async(
            agents=agents,
            prompt_context=prompt_context,
            model=_agent_model(),
            api_key=api_key,
            task_overrides=task_overrides,
        )
    )


_ECHO_STOPWORDS = frozenset(
    "the a an and or but if of to in on for with is are was be been being it its this that "
    "these those i you he she we they me my your our their as at by from not no yes so just "
    "really very what who how why when where all any can will would should could do does did "
    "done have has had get got make made like want need one two thing things".split()
)


def _echo_tokens(text: str) -> set[str]:
    words = re.sub(r"[^a-z0-9\s]", " ", text.lower()).split()
    return {w for w in words if len(w) >= 3 and w not in _ECHO_STOPWORDS}


def _echo_anchors(text: str) -> set[str]:
    """Timestamps are strong echo signals: two takes pointing at the same
    moment with overlapping wording are the same take, however reworded."""
    return set(re.findall(r"\b\d{1,2}:\d{2}\b", text.lower()))


def _take_is_echo(message: str, prior_takes: list[str]) -> bool:
    """True when a take substantially repeats an earlier take from this round."""
    mine = _echo_tokens(message)
    if not mine:
        return False
    my_anchors = _echo_anchors(message)
    for prior in prior_takes:
        theirs = _echo_tokens(prior)
        if not theirs:
            continue
        union = len(mine | theirs)
        overlap = len(mine & theirs) / union if union else 0.0
        if overlap >= 0.45:
            return True
        if len(theirs & mine) / len(theirs) >= 0.8:
            return True
        # Same timestamp anchor + modest word overlap = reworded echo.
        if my_anchors and (my_anchors & _echo_anchors(prior)) and overlap >= 0.15:
            return True
    return False


def _round_transcript_block(transcript: list[tuple[str, str]]) -> str:
    lines = [f"- {AGENT_DISPLAY_NAMES.get(a, a)}: {m}" for a, m in transcript]
    return (
        "TAKES ALREADY GIVEN THIS ROUND (never repeat these; respond to them, "
        "counter someone by name, or add a new angle):\n" + "\n".join(lines)
    )


def _round_context(
    prompt_context: str,
    trigger_text: str,
    transcript: list[tuple[str, str]],
) -> str:
    parts: list[str] = []
    if trigger_text:
        parts.append(f'THE ARTIST JUST SAID: "{trigger_text}"')
    if transcript:
        parts.append(_round_transcript_block(transcript))
    if not parts:
        return prompt_context
    return prompt_context + "\n\n" + "\n\n".join(parts)


def _selector_system_prompt() -> str:
    lanes = "\n".join(f"- {key}: {AGENT_LENSES[key]}" for key in ROUNDTABLE_AGENTS)
    return (
        "You are the moderator of a record-label roundtable between an artist and the label team.\n"
        "Your only job is to pick who speaks next. You never speak yourself.\n"
        "\n"
        "ROSTER AND LANES:\n"
        f"{lanes}\n"
        "\n"
        "RULES:\n"
        "- Choose ONLY from the remaining agents listed in the situation, one speaker per answer.\n"
        "- If the artist addressed someone by name or role, that person speaks first.\n"
        "- Skip anyone whose lane is already covered unless they would genuinely counter a specific point that was made.\n"
        "- When the useful takes are in and the artist needs ONE decision or next step stated, choose manager_summary (Dez closes; he decides, he does not repeat the room).\n"
        "- When nothing new or decisive is left to say, choose stop. Fewer, sharper voices beat everyone talking.\n"
        "\n"
        'Return ONLY valid JSON: {"next": "<agent_key|manager_summary|stop>"}'
    )


async def _select_next_speaker_async(
    *,
    remaining: list[str],
    transcript: list[tuple[str, str]],
    trigger_text: str,
    stage_label: str,
    turns_left: int,
    allow_manager_summary: bool,
    model: str,
    api_key: str,
    min_turns: int = 0,
) -> str:
    if not remaining:
        return "stop"
    if transcript:
        takes_text = "\n".join(f"- {AGENT_DISPLAY_NAMES.get(a, a)}: {m}" for a, m in transcript)
    else:
        takes_text = "(none yet)"
    user_prompt = (
        f"SITUATION: {stage_label}\n"
        f"ARTIST SAID: {trigger_text or '(a new track just dropped for its first listen)'}\n"
        f"REMAINING AGENTS: {', '.join(remaining)}\n"
        f"TURNS REMAINING: {turns_left}\n"
        f"TAKES SO FAR:\n{takes_text}\n\n"
        "Who speaks next?"
    )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            response = await client.post(
                OPENROUTER_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://ai-record-label.local",
                    "X-Title": "AI Record Label Agent Voices",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _selector_system_prompt()},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 64,
                    "response_format": {"type": "json_object"},
                    "reasoning": {"enabled": False, "exclude": True},
                },
            )
        if response.is_error:
            return "stop"
        parsed = json.loads(str(response.json()["choices"][0]["message"]["content"]))
        pick = str(parsed.get("next", "stop")).strip().lower()
    except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError, TypeError):
        return "stop"
    if pick == "manager_summary":
        return "manager_summary" if allow_manager_summary else "stop"
    if pick in remaining:
        return pick
    if len(transcript) < min_turns and remaining:
        # Debates need at least a back-and-forth; a premature stop would leave
        # a monologue. Force a fresh voice instead.
        return remaining[0]
    return "stop"


async def _run_roundtable_round_async(
    *,
    prompt_context: str,
    trigger_text: str,
    stage_label: str,
    candidate_agents: list[str],
    model: str,
    api_key: str,
    max_turns: int,
    allow_manager_summary: bool,
    require_all_agents: bool,
    persist: Any,
    audience: str = "artist",
    min_turns: int = 0,
    prior_takes: tuple[tuple[str, str], ...] = (),
    track_duration: float | None = None,
) -> list[dict[str, Any]]:
    remaining = [agent for agent in candidate_agents if agent in AGENT_LENSES]
    transcript: list[tuple[str, str]] = []
    results: list[dict[str, Any]] = []
    for turn in range(max_turns):
        if not remaining:
            break
        pick = await _select_next_speaker_async(
            remaining=remaining,
            transcript=transcript,
            trigger_text=trigger_text,
            stage_label=stage_label,
            turns_left=max_turns - turn,
            allow_manager_summary=allow_manager_summary and "manager" in candidate_agents,
            model=model,
            api_key=api_key,
            min_turns=min_turns,
        )
        if pick == "stop" and require_all_agents:
            pick = remaining[0]
        force_summary = pick == "manager_summary"
        if force_summary:
            if "manager" in [spoken_agent for spoken_agent, _ in transcript]:
                if not require_all_agents:
                    break
                force_summary = False
                agent = remaining[0]
            else:
                agent = "manager"
        elif pick == "stop":
            break
        else:
            agent = pick

        task_override: str | None = None
        if force_summary and transcript:
            room_lines = "\n".join(
                f"- {AGENT_DISPLAY_NAMES.get(a, a)}: {m}" for a, m in transcript
            )
            task_override = (
                "You are writing Dez's close-out of this roundtable round. The room's takes:\n"
                f"{room_lines}\n"
                "State the decision in plain language and name the single next move and its owner. "
                "Do NOT restate each person's point; the artist just heard them all. "
                "Close with one line that opens the floor to the artist - invite them to ask "
                "anything or make the call. Max 120 words."
            )
        elif audience == "room" and transcript:
            prev_agent = transcript[-1][0]
            task_override = (
                f"{AGENT_TASKS.get(agent, '')} "
                f"You are directly responding to {AGENT_DISPLAY_NAMES.get(prev_agent, prev_agent)}, "
                "who just spoke. Address them by name, agree or push back, then add your own angle."
            ).strip()
        message, timestamp_sec = await _generate_agent_message_async(
            agent=agent,
            prompt_context=_round_context(prompt_context, trigger_text, transcript),
            model=model,
            api_key=api_key,
            task_override=task_override,
            audience=audience,
            track_duration=track_duration,
        )
        if agent in remaining:
            remaining.remove(agent)
        if not message.strip():
            continue  # this voice chose to sit the round out
        full_transcript = [*prior_takes, *transcript]
        if not force_summary and _take_is_echo(
            message, [prior_message for _, prior_message in full_transcript]
        ):
            counters = ", ".join(
                AGENT_DISPLAY_NAMES.get(a, a) for a, _ in full_transcript[-3:]
            ) or "the room"
            retry_message, retry_timestamp = await _generate_agent_message_async(
                agent=agent,
                prompt_context=_round_context(prompt_context, trigger_text, transcript),
                model=model,
                api_key=api_key,
                audience=audience,
                track_duration=track_duration,
                task_override=(
                    f"That take was too close to what {counters} already said. Do NOT restate it. "
                    "Bring the sharpest angle from YOUR own lane that nobody has touched yet, "
                    "or push back on them by name. "
                    'If you truly have nothing new to add, reply with {"message": ""}.'
                ),
            )
            if retry_message.strip() and not _take_is_echo(
                retry_message, [prior_message for _, prior_message in full_transcript]
            ):
                message, timestamp_sec = retry_message, retry_timestamp
            elif not require_all_agents:
                continue  # better silent than a duplicate
            elif retry_message.strip():
                # Forced roster: the retry was explicitly told to differentiate,
                # so prefer it over the original echo.
                message, timestamp_sec = retry_message, retry_timestamp
        feedback_id = persist(agent, message, timestamp_sec)
        transcript.append((agent, message))
        results.append({"agent": agent, "message": message, "feedback_id": feedback_id})
        if force_summary:
            break  # Dez closed the round; nobody speaks after the decision
    return results


def _prewarm_take_voice(data_dir: Path, feedback_id: int, agent: str, message: str) -> None:
    """Generate this take's TTS ahead of playback so the voice plays instantly.

    Runs while the next take is still being generated. A failure is logged and
    left to the on-demand /tts path, which synthesizes on first play.
    """
    try:
        from audio_analysis.tts import synthesize

        synthesize(data_dir, feedback_id, agent, message)
    except Exception:
        logger.exception("TTS prewarm failed for message %d (%s)", feedback_id, agent)


def _run_roundtable_round(
    *,
    db_path: str,
    track_id: int | None,
    project_id: int | None,
    prompt_context: str,
    trigger_text: str,
    stage_label: str,
    candidate_agents: list[str],
    max_turns: int = ROUND_MAX_TURNS,
    allow_manager_summary: bool = True,
    require_all_agents: bool = False,
    default_intent: str = "roundtable_reply",
    intents: dict[str, str] | None = None,
    channel: str = "desktop",
    audience: str = "artist",
    min_turns: int = 0,
    prior_takes: tuple[tuple[str, str], ...] = (),
) -> list[dict[str, Any]]:
    """Run one moderated roundtable round: sequential selector-driven turns,
    each take persisted as it lands so voices play back in conversation order."""

    def persist_take(agent: str, message: str, timestamp_sec: float | None = None) -> int | None:
        intent = (intents or {}).get(agent, default_intent)
        with _db_conn(db_path) as conn, conn:
            feedback_id = _upsert_feedback_message(
                conn,
                track_id=track_id,
                project_id=project_id,
                agent=agent,
                message=message,
                channel=channel,
                direction="outbound",
                intent=intent,
                timestamp_sec=timestamp_sec,
            )
        if feedback_id is not None:
            tts_executor.submit(
                _prewarm_take_voice, Path(db_path).parent, feedback_id, agent, message
            )
        return feedback_id

    track_duration = _track_duration_sec(db_path, track_id)
    api_key = _openrouter_key(os.environ.get("OPENROUTER_API_KEY"))
    tts_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tts-prewarm")
    try:
        return asyncio.run(
            _run_roundtable_round_async(
                prompt_context=prompt_context,
                trigger_text=trigger_text,
                stage_label=stage_label,
                candidate_agents=candidate_agents,
                model=_agent_model(),
                api_key=api_key,
                max_turns=max_turns,
                allow_manager_summary=allow_manager_summary,
                require_all_agents=require_all_agents,
                audience=audience,
                min_turns=min_turns,
                persist=persist_take,
                prior_takes=prior_takes,
                track_duration=track_duration,
            )
        )
    finally:
        tts_executor.shutdown(wait=True)


def _track_duration_sec(db_path: str, track_id: int | None) -> float | None:
    if track_id is None:
        return None
    with _db_conn(db_path) as conn:
        row = conn.execute(
            "SELECT duration_seconds FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
    if row is None or row["duration_seconds"] is None:
        return None
    return float(row["duration_seconds"])


def _tracks_in_same_folder(conn: sqlite3.Connection, file_path: str) -> list[sqlite3.Row]:
    parent = str(Path(file_path).parent)
    rows = conn.execute(
        "SELECT id, title, file_path, project_id FROM tracks ORDER BY id"
    ).fetchall()
    return [row for row in rows if str(Path(str(row["file_path"])).parent) == parent]


def _project_type(track_count: int) -> str:
    if track_count <= 1:
        return "single"
    if track_count <= 5:
        return "ep"
    return "album"


def _ensure_project(conn: sqlite3.Connection, track: sqlite3.Row) -> int | None:
    if not _table_exists(conn, "projects"):
        return None
    current_project_id = track["project_id"] if "project_id" in tuple(track.keys()) else None
    if current_project_id is not None:
        return int(current_project_id)

    folder_tracks = _tracks_in_same_folder(conn, str(track["file_path"]))
    track_count = max(1, len(folder_tracks))
    project_type = _project_type(track_count)
    project_title = (
        _track_title(track)
        if project_type == "single"
        else Path(str(track["file_path"])).parent.name
    )
    if not project_title:
        raise PipelineError(
            "Cannot create project: track file_path has no usable title or parent folder"
        )

    existing = conn.execute(
        "SELECT id FROM projects WHERE title = ? AND type = ? ORDER BY id DESC LIMIT 1",
        (project_title, project_type),
    ).fetchone()
    if existing:
        project_id = int(existing["id"])
    else:
        cur = conn.execute(
            """INSERT INTO projects (title, type, state, target_track_count)
               VALUES (?, ?, 'active', ?)""",
            (project_title, project_type, track_count),
        )
        if cur.lastrowid is None:
            raise RuntimeError("INSERT into projects did not return a lastrowid")
        project_id = cur.lastrowid

    conn.execute(
        "UPDATE tracks SET project_id = ?, updated_at = datetime('now') WHERE id = ?",
        (project_id, int(track["id"])),
    )
    for sibling in folder_tracks:
        if sibling["project_id"] is None:
            conn.execute(
                "UPDATE tracks SET project_id = ?, updated_at = datetime('now') WHERE id = ?",
                (project_id, int(sibling["id"])),
            )
    return project_id


def _insert_comment(
    conn: sqlite3.Connection,
    *,
    track_id: int,
    author: str,
    body: str,
    timestamp_s: float | None = None,
) -> None:
    if not _table_exists(conn, "track_comments"):
        return
    exists = conn.execute(
        """SELECT 1 FROM track_comments
           WHERE track_id = ? AND author = ? AND body = ? LIMIT 1""",
        (track_id, author, body),
    ).fetchone()
    if exists:
        return
    conn.execute(
        """INSERT INTO track_comments (track_id, version_id, timestamp_s, author, body)
           VALUES (?, NULL, ?, ?, ?)""",
        (track_id, timestamp_s, author, body),
    )


def _upsert_kg_node(
    conn: sqlite3.Connection,
    *,
    node_id: str,
    node_type: str,
    label: str,
    properties: dict[str, Any],
) -> None:
    conn.execute(
        """INSERT INTO kg_nodes (id, type, label, properties, updated_at)
           VALUES (?, ?, ?, ?, datetime('now'))
           ON CONFLICT(id) DO UPDATE SET
             label = excluded.label,
             properties = excluded.properties,
             updated_at = excluded.updated_at""",
        (node_id, node_type, label, json.dumps(properties, sort_keys=True)),
    )


def _upsert_kg_edge(
    conn: sqlite3.Connection,
    *,
    source: str,
    target: str,
    relation: str,
    weight: float = 1.0,
    properties: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """INSERT INTO kg_edges (source, target, relation, weight, properties)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(source, target, relation) DO UPDATE SET
             weight = excluded.weight,
             properties = excluded.properties""",
        (source, target, relation, weight, json.dumps(properties or {}, sort_keys=True)),
    )


def _write_knowledge_graph(
    conn: sqlite3.Connection,
    *,
    track: sqlite3.Row,
    project_id: int | None,
    analysis: AudioAnalysis | None,
) -> None:
    if not (_table_exists(conn, "kg_nodes") and _table_exists(conn, "kg_edges")):
        return

    track_id = int(track["id"])
    track_node = f"track:{track_id}"
    _upsert_kg_node(
        conn,
        node_id=track_node,
        node_type="track",
        label=_track_title(track),
        properties={
            "track_id": track_id,
            "file_path": str(track["file_path"]),
            "state": str(track["state"]),
            "project_id": project_id,
        },
    )

    if project_id is not None:
        project = conn.execute(
            "SELECT id, title, type, state FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if project is None:
            raise PipelineError(f"Cannot write KG: project {project_id} does not exist")
        project_node = f"project:{project_id}"
        _upsert_kg_node(
            conn,
            node_id=project_node,
            node_type="project",
            label=str(project["title"]),
            properties={
                "project_id": project_id,
                "type": project["type"],
                "state": project["state"],
            },
        )
        _upsert_kg_edge(conn, source=project_node, target=track_node, relation="contains_track")

    if analysis is None:
        return
    for tag in analysis.genre_tags:
        genre_node = f"genre:{tag.strip().lower().replace(' ', '_')}"
        _upsert_kg_node(conn, node_id=genre_node, node_type="genre", label=tag, properties={})
        _upsert_kg_edge(
            conn,
            source=track_node,
            target=genre_node,
            relation="has_genre",
            weight=0.8,
        )
    for tag in analysis.mood_tags:
        mood_node = f"mood:{tag.strip().lower().replace(' ', '_')}"
        _upsert_kg_node(conn, node_id=mood_node, node_type="mood", label=tag, properties={})
        _upsert_kg_edge(conn, source=track_node, target=mood_node, relation="has_mood", weight=0.8)


def _write_intake_side_effects(
    conn: sqlite3.Connection,
    *,
    track: sqlite3.Row,
    project_id: int | None,
) -> None:
    track_id = int(track["id"])
    project_text = f" project_id={project_id}." if project_id is not None else ""
    _insert_feedback(
        conn,
        track_id=track_id,
        project_id=project_id,
        agent="intake",
        intent="intake_complete",
        message=f"Intake complete for {_track_title(track)}.{project_text} Ready for A&R review.",
    )
    _insert_comment(
        conn,
        track_id=track_id,
        author="intake",
        body=f"Automatic intake complete.{project_text}".strip(),
    )
    _write_knowledge_graph(conn, track=track, project_id=project_id, analysis=None)


def _stems_base(db_path: str) -> Path:
    data_dir = os.environ.get("AI_RECORD_LABEL_DATA")
    if data_dir:
        return Path(data_dir) / "stems"
    return Path(db_path).resolve().parent / "stems"


def _run_stem_separation(file_path: str, stems_base: Path) -> dict[str, str]:
    try:
        return asyncio.run(separate_stems(file_path, stems_base, force=False))
    except (FileNotFoundError, RuntimeError, StemSeparatorError) as exc:
        raise PipelineError(f"Stem separation failed: {exc}") from exc


def _trigger_segment_analysis(
    conn: sqlite3.Connection,
    *,
    db_path: str,
    track_id: int,
    file_path: str,
) -> bool:
    """Run granular segment analysis if not already present for this track.

    Segments feed the verdict synthesizer and Maren's artwork pipeline. A
    failure here is real — propagated as PipelineError so the caller writes
    a visible pipeline_error feedback row and the user sees what broke.
    """
    if not _table_exists(conn, "track_segments"):
        raise PipelineError("track_segments table is missing — migration 013 has not been applied")
    existing = conn.execute(
        "SELECT 1 FROM track_segments WHERE track_id = ? LIMIT 1",
        (track_id,),
    ).fetchone()
    if existing:
        return False
    try:
        asyncio.run(analyze_segments(file_path, db_path, track_id))
    except SegmentAnalysisError as exc:
        raise PipelineError(f"Segment analysis failed: {exc}") from exc
    return True


def _trigger_segment_analysis_isolated(*, db_path: str, track_id: int, file_path: str) -> bool:
    """Run segment analysis on its own SQLite connection for safe overlap with local work."""
    with _db_conn(db_path) as conn:
        return _trigger_segment_analysis(
            conn,
            db_path=db_path,
            track_id=track_id,
            file_path=file_path,
        )


def _trigger_audio_features(
    conn: sqlite3.Connection,
    *,
    db_path: str,
    track_id: int,
    file_path: str,
) -> bool:
    """Extract track-level audio features (BPM, key, spectral, dynamics) if
    not already present for this track.

    Failures are propagated as PipelineError so the caller writes a visible
    pipeline_error feedback row.
    """
    if not _table_exists(conn, "track_audio_features"):
        raise PipelineError(
            "track_audio_features table is missing — migration 014 has not been applied"
        )
    existing = conn.execute(
        "SELECT 1 FROM track_audio_features WHERE track_id = ? LIMIT 1",
        (track_id,),
    ).fetchone()
    if existing:
        return False
    try:
        extract_audio_features(file_path, db_path, track_id)
    except FeatureExtractionError as exc:
        raise PipelineError(f"Audio feature extraction failed: {exc}") from exc
    return True


def _trigger_embedding(
    conn: sqlite3.Connection,
    *,
    db_path: str,
    track_id: int,
    file_path: str,
) -> bool:
    """Extract the CNN14 PANNs embedding if not already present.

    Failures are propagated as PipelineError.
    """
    if not _table_exists(conn, "track_audio_embeddings"):
        raise PipelineError(
            "track_audio_embeddings table is missing — migration 015 has not been applied"
        )
    existing = conn.execute(
        "SELECT 1 FROM track_audio_embeddings WHERE track_id = ? LIMIT 1",
        (track_id,),
    ).fetchone()
    if existing:
        return False
    try:
        extract_embedding(file_path, db_path, track_id)
    except EmbeddingExtractionError as exc:
        raise PipelineError(f"Embedding extraction failed: {exc}") from exc
    return True


def _trigger_maren_artwork(
    conn: sqlite3.Connection,
    *,
    db_path: str,
    track_id: int,
) -> bool:
    """Fire Maren's NanoBanana orchestrator if no variants exist yet.

    Runs in a background thread (cannot raise into the main dispatcher
    response). Failures are surfaced as a visible feedback row from `system`
    with intent `pipeline_error` so the user sees the failure in the
    roundtable — never silently swallowed.
    """
    if not _table_exists(conn, "artwork_generations"):
        _insert_feedback(
            conn,
            track_id=track_id,
            project_id=None,
            agent="system",
            intent="pipeline_error",
            message=(
                "Cannot generate cover art: artwork_generations table missing "
                "(migration 013 not applied)."
            ),
        )
        return False
    existing = conn.execute(
        "SELECT 1 FROM artwork_generations WHERE track_id = ? LIMIT 1",
        (track_id,),
    ).fetchone()
    if existing:
        return False
    try:
        asyncio.run(generate_artwork_variants(db_path, track_id))
    except MarenOrchestrationError as exc:
        logger.exception("Maren artwork generation failed for track %d", track_id)
        _insert_feedback(
            conn,
            track_id=track_id,
            project_id=None,
            agent="system",
            intent="pipeline_error",
            message=f"Maren could not generate cover variants: {exc}",
        )
        return False
    return True


def _trigger_stem_separation(
    conn: sqlite3.Connection,
    *,
    db_path: str,
    track_id: int,
    file_path: str,
) -> bool:
    if not _table_exists(conn, "track_stems"):
        return False
    existing = conn.execute(
        "SELECT 1 FROM track_stems WHERE track_id = ? AND model = 'htdemucs' LIMIT 1",
        (track_id,),
    ).fetchone()
    if existing:
        return False

    stems = _run_stem_separation(file_path, _stems_base(db_path))
    missing = [stem for stem in STEM_NAMES if stem not in stems]
    if missing:
        raise PipelineError(f"Stem separation failed: missing stem outputs: {', '.join(missing)}")
    # Commit immediately: later triggers in the same pipeline pass (segment
    # analysis, feature extraction, embeddings) each open their own separate
    # connection to write. Leaving this INSERT uncommitted on the shared
    # `conn` blocks every one of them until they time out and error.
    with conn:
        conn.execute(
            """INSERT INTO track_stems (track_id, model, vocals_path, drums_path, bass_path, other_path)
               VALUES (?, 'htdemucs', ?, ?, ?, ?)""",
            (
                track_id,
                stems.get("vocals"),
                stems.get("drums"),
                stems.get("bass"),
                stems.get("other"),
            ),
        )
    return True


def run_intake_rounds(
    *,
    db_path: str,
    track_id: int,
    project_id: int | None,
    prompt_context: str,
    timings_ms: dict[str, float] | None = None,
) -> list[str]:
    """The post-analysis meeting, in three acts:

    1. Review round - the music execs give their first reads to the artist.
    2. Room session - the agents talk it out between themselves, one voice
       at a time, pulling in Maren (creative) or the release desk (bandcamp)
       when their lane is needed; they may turn and ask the artist directly.
    3. Close - Dez states the decision and opens the floor to the artist.
    """
    actions: list[str] = []
    try:
        # Act 1 - first reads to the artist.
        stage_started = perf_counter()
        act_one = _run_roundtable_round(
            db_path=db_path,
            track_id=track_id,
            project_id=project_id,
            prompt_context=prompt_context,
            trigger_text="",
            stage_label="new_track_first_listen",
            candidate_agents=MUSIC_EXECS,
            max_turns=ROUND_MAX_TURNS,
            allow_manager_summary=False,
            require_all_agents=True,
            default_intent="analysis_feedback",
            intents=ROUND_TABLE_INTENTS,
        )
        if timings_ms is not None:
            timings_ms["intake_review_round"] = round((perf_counter() - stage_started) * 1000, 1)

        # Act 2 - the room talks it out between themselves.
        stage_started = perf_counter()
        act_two = _run_roundtable_round(
            db_path=db_path,
            track_id=track_id,
            project_id=project_id,
            prompt_context=prompt_context,
            trigger_text=(
                "First listens are done and the artist is listening in. Take the room and talk it "
                "out between yourselves - agree, push back by name, defend your point. Pull in "
                "Maren when the visuals or the identity need the creative eye, and the release "
                "desk (bandcamp) when release, distribution, or storefront matters come up. If a "
                "point needs the artist's own input, turn and ask them directly."
            ),
            stage_label="new_track_room_session",
            candidate_agents=MUSIC_EXECS + ["creative_director", "bandcamp"],
            max_turns=6,
            allow_manager_summary=False,
            require_all_agents=False,
            default_intent="room_discussion",
            audience="room",
            min_turns=4,
            prior_takes=tuple((r["agent"], r["message"]) for r in act_one),
        )
        if timings_ms is not None:
            timings_ms["intake_room_session"] = round((perf_counter() - stage_started) * 1000, 1)

        # Act 3 - Dez closes and opens the floor.
        stage_started = perf_counter()
        act_three = _run_roundtable_round(
            db_path=db_path,
            track_id=track_id,
            project_id=project_id,
            prompt_context=prompt_context,
            trigger_text=(
                "The room has talked it out. Deliver the close: the decision, what changes if "
                "anything, and open the floor to the artist."
            ),
            stage_label="new_track_close",
            candidate_agents=["manager"],
            max_turns=1,
            allow_manager_summary=True,
            require_all_agents=True,
            default_intent="review_round_summary",
            intents=ROUND_TABLE_INTENTS,
        )
        if timings_ms is not None:
            timings_ms["intake_close"] = round((perf_counter() - stage_started) * 1000, 1)
    except (AgentVoiceError, httpx.HTTPError) as exc:
        raise PipelineError(f"Agent voice generation failed: {exc}") from exc

    actions.extend(f"{result['agent']}_review" for result in [*act_one, *act_two, *act_three])
    return actions


def _write_post_analysis_actions(
    conn: sqlite3.Connection,
    *,
    db_path: str,
    track: sqlite3.Row,
    project_id: int | None,
    analysis: AudioAnalysis | None,
    timings_ms: dict[str, float] | None = None,
) -> list[str]:
    track_id = int(track["id"])
    actions: list[str] = []
    # Commit immediately — left open, this blocks every trigger below that
    # opens its own separate connection to write (segment analysis, feature
    # extraction, embeddings).
    with conn:
        conn.execute(
            """DELETE FROM feedback
               WHERE track_id = ?
                 AND intent IN (
                   'vision_assessment_requested',
                   'cultural_authenticity_requested',
                   'essential_question_requested'
                 )""",
            (track_id,),
        )
    # Segment analysis is primarily a network wait. Start it now so that wait overlaps
    # the independent local stem/features/embedding work. Its failure is still raised
    # only after the durable local work completes.
    segment_started = perf_counter()
    segment_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="segment-analysis")
    segment_future = segment_executor.submit(
        _trigger_segment_analysis_isolated,
        db_path=db_path,
        track_id=track_id,
        file_path=str(track["file_path"]),
    )
    stage_started = perf_counter()
    stems_created = _trigger_stem_separation(
        conn,
        db_path=db_path,
        track_id=track_id,
        file_path=str(track["file_path"]),
    )
    if timings_ms is not None:
        timings_ms["stem_separation"] = round((perf_counter() - stage_started) * 1000, 1)
    if stems_created:
        actions.append("separate_stems")
        # Commit immediately — same reason as the DELETE above: left open,
        # this blocks _trigger_segment_analysis's separate connection for
        # its entire external-API-calling duration.
        with conn:
            _insert_comment(
                conn,
                track_id=track_id,
                author="system",
                body="Stem separation completed automatically after audio analysis.",
            )
            _insert_feedback(
                conn,
                track_id=track_id,
                project_id=project_id,
                agent="system",
                intent="stems_ready",
                message=(
                    "Stem separation is complete. Janick, Rhone, and Rubin can review the "
                    "non-release next steps."
                ),
            )

    # Complete deterministic local work before paid/network analysis so a provider
    # outage cannot prevent features and embeddings from being vaulted with the track.
    stage_started = perf_counter()
    features_created = _trigger_audio_features(
        conn,
        db_path=db_path,
        track_id=track_id,
        file_path=str(track["file_path"]),
    )
    if timings_ms is not None:
        timings_ms["audio_features"] = round((perf_counter() - stage_started) * 1000, 1)
    if features_created:
        actions.append("extract_audio_features")

    stage_started = perf_counter()
    embedding_created = _trigger_embedding(
        conn,
        db_path=db_path,
        track_id=track_id,
        file_path=str(track["file_path"]),
    )
    if timings_ms is not None:
        timings_ms["embedding"] = round((perf_counter() - stage_started) * 1000, 1)
    if embedding_created:
        actions.append("extract_embedding")

    try:
        segments_created = segment_future.result()
    finally:
        segment_executor.shutdown(wait=True)
    if timings_ms is not None:
        timings_ms["segment_analysis"] = round((perf_counter() - segment_started) * 1000, 1)
    if segments_created:
        actions.append("analyze_segments")

    with conn:
        _write_knowledge_graph(conn, track=track, project_id=project_id, analysis=analysis)
    if analysis is None:
        raise PipelineError("Post-analysis review generation requires a real audio analysis")

    prompt_context = _track_prompt_context(
        conn,
        track=track,
        project_id=project_id,
        analysis=analysis,
        stage="post_analysis_review_round",
    )
    actions.extend(
        run_intake_rounds(
            db_path=db_path,
            track_id=track_id,
            project_id=project_id,
            prompt_context=prompt_context,
            timings_ms=timings_ms,
        )
    )
    return actions


def _manager_intake_message(project_title: str, track_count: int) -> str:
    unit = "track" if track_count == 1 else "tracks"
    return (
        f"Intake is complete: {track_count} {unit} under {project_title}. A&R review is underway."
    )


def _manager_panel_message(panelist_count: int) -> str:
    if panelist_count > 0:
        return f"Human QC panel session opened with {panelist_count} active listener(s)."
    return (
        "Human QC panel is ready in the workflow, but there are no active panel "
        "listeners configured yet."
    )


def _timeout_feedback_stale_message() -> str:
    return (
        "It has been sitting for a minute. If you want changes, send the revision. "
        "If this version is the one, say approve and I will move it forward."
    )


def _timeout_art_overdue_message(release_date: str | None = None) -> str:
    if release_date:
        return (
            f"Artwork is still the open gate and the target release date is {release_date}. "
            "Send the cover direction or artwork so this does not stall."
        )
    return (
        "Artwork is still the open gate on this release. Send the cover direction or "
        "artwork so this can move."
    )


def _timeout_release_missed_message(release_date: str | None = None) -> str:
    if release_date:
        return (
            f"We missed the {release_date} release window. Give me the new target date "
            "or I will hold this in release-ready until you set one."
        )
    return "We missed the release window. Send me the new date and I will reset the schedule."


def _artist_revision_ack_message() -> str:
    return "Copy. Keep the current notes in mind and send the new version when it is ready."


def _artist_delay_ack_message(date_hint: str | None = None) -> str:
    if date_hint:
        return f"Understood. I will treat {date_hint} as the working target unless you change it."
    return "Understood. I will hold the timeline until you give me the next date."


def _artist_question_ack_message() -> str:
    return "I saw your question. I am pulling the current track context together now."


def _artist_clarification_message() -> str:
    return "I need a clean yes or no on this version: approve it, or tell me you are revising."


class TrackPipelineDispatcher:
    """Process release-pipeline events against the SQLite database."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        _load_dotenv()

    def __call__(self, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.process_event(event, payload)

    def process_event(self, event: str, payload: dict[str, Any]) -> dict[str, Any]:
        if event == "new_track_detected":
            return self.process_new_track(payload)
        if event in {"track_approved", "artist_approves"}:
            return self.process_track_approved(payload, event=event)
        if event == "artist_message_inbound":
            return self.process_artist_message(payload)
        if event == "agent_debate_requested":
            return self.process_debate_request(payload)
        if event == "revision_uploaded":
            return self.process_revision_uploaded(payload)
        if event == "weekly_summary_due":
            return self.process_weekly_summary(payload)
        if event in {
            "conductor_message_approved",
            "conductor_message_delivered",
            "conductor_summary_delivered",
            "pending_message_approved",
        }:
            return self.process_conductor_message_delivered(payload, event=event)
        if event == "timeout_feedback_stale":
            return self.process_timeout_feedback_stale(payload)
        if event == "timeout_art_overdue":
            return self.process_timeout_art_overdue(payload)
        if event == "timeout_release_date_missed":
            return self.process_timeout_release_date_missed(payload)
        if event == "catalog_memory_refresh":
            return self.process_catalog_memory_refresh(payload)
        logger.debug("No coordination handler for event=%s", event)
        return {"event": event, "handled": False}

    def _resolve_track(self, conn: sqlite3.Connection, payload: dict[str, Any]) -> sqlite3.Row:
        track_id = payload.get("track_id")
        row = None
        if track_id is not None:
            try:
                row = conn.execute("SELECT * FROM tracks WHERE id = ?", (int(track_id),)).fetchone()
            except (TypeError, ValueError) as exc:
                raise PipelineError("payload.track_id must be an integer") from exc
        elif payload.get("file_path"):
            row = conn.execute(
                "SELECT * FROM tracks WHERE file_path = ? ORDER BY id DESC LIMIT 1",
                (str(payload["file_path"]),),
            ).fetchone()
        if row is None:
            raise PipelineError("Track not found for coordination event")
        return row

    def _project_id_for_track(
        self,
        conn: sqlite3.Connection,
        track: sqlite3.Row | None,
    ) -> int | None:
        if track is None:
            return None
        project_id = track["project_id"] if "project_id" in tuple(track.keys()) else None
        if project_id is not None:
            return int(project_id)
        return _ensure_project(conn, track)

    def _ensure_intake(self, conn: sqlite3.Connection, track: sqlite3.Row) -> dict[str, Any]:
        track_id = int(track["id"])
        project_id = _ensure_project(conn, track)
        refreshed = self._resolve_track(conn, {"track_id": track_id})
        _write_intake_side_effects(conn, track=refreshed, project_id=project_id)

        project_title = _track_title(refreshed)
        track_count = 1
        if project_id is not None:
            project = conn.execute(
                "SELECT id, title FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if project is None:
                raise PipelineError(f"Cannot complete intake: project {project_id} does not exist")
            project_title = str(project["title"])
            track_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM tracks WHERE project_id = ?",
                    (project_id,),
                ).fetchone()[0]
            )
            _insert_feedback(
                conn,
                track_id=track_id,
                project_id=project_id,
                agent="manager",
                intent="manager_intake_complete",
                message=_manager_intake_message(project_title, track_count),
            )
        return {
            "project_id": project_id,
            "project_title": project_title,
            "track_count": track_count,
        }

    def _ensure_panel_session(self, conn: sqlite3.Connection, track_id: int) -> dict[str, Any]:
        if not _table_exists(conn, "panel_sessions"):
            return {"panel_session_id": None, "panelist_count": 0}
        existing = conn.execute(
            "SELECT id, status FROM panel_sessions WHERE track_id = ? ORDER BY id DESC LIMIT 1",
            (track_id,),
        ).fetchone()
        panelist_count = 0
        if _table_exists(conn, "listening_panel"):
            panelist_count = int(
                conn.execute("SELECT COUNT(*) FROM listening_panel WHERE active = 1").fetchone()[0]
            )
        if existing:
            return {
                "panel_session_id": int(existing["id"]),
                "panelist_count": panelist_count,
            }

        status = "sent" if panelist_count > 0 else "waiting_for_panelists"
        cursor = conn.execute(
            "INSERT INTO panel_sessions (track_id, status, summary) VALUES (?, ?, ?)",
            (track_id, status, _manager_panel_message(panelist_count)),
        )
        _insert_feedback(
            conn,
            track_id=track_id,
            agent="manager",
            intent="panel_session_started",
            message=_manager_panel_message(panelist_count),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("INSERT into panel_sessions did not return a lastrowid")
        return {"panel_session_id": cursor.lastrowid, "panelist_count": panelist_count}

    def _notify_track_approved_agents(
        self,
        conn: sqlite3.Connection,
        track_id: int,
        *,
        project_id: int | None,
    ) -> None:
        track = self._resolve_track(conn, {"track_id": track_id})
        analysis = _latest_analysis(conn, track_id)
        prompt_context = _track_prompt_context(
            conn,
            track=track,
            project_id=project_id,
            analysis=analysis,
            stage="track_approved_artwork_gate",
        )
        try:
            generated = _generate_agent_message_bundle(
                agents=["manager", "creative_director", "kallman", "janick", "rhone", "rubin"],
                prompt_context=prompt_context,
                task_overrides={
                    "manager": (
                        "You are writing Dez's approved-track handoff. "
                        "A&R already approved the song; now state plainly that artwork is the only open gate and what the artist needs to send next. "
                        "Do not invent dates or deadlines. 2-3 short sentences, direct and human."
                    ),
                    "creative_director": (
                        "You are writing Maren's first artwork-gate note after approval. "
                        "Translate the approved track's mood and identity into visual direction, "
                        "connect it to the artist's visual world if the context supports that, "
                        "then ask for references, sketches, photos, or a written vibe. "
                        "Do not claim artwork exists unless the context says it does. Avoid generic color-board filler."
                    ),
                    "kallman": (
                        "You are writing Craig Kallman's short follow-up after approval. "
                        "Comment on whether the approved record still has that early-conviction signal and whether there is a larger signal here. "
                        "Stay decisive, not analytical. Do not talk about structure, worldbuilding, or technicalities. 1-2 short lowercase sentences."
                    ),
                    "janick": (
                        "You are writing John Janick's approved-track vision note. "
                        "Judge whether this approved song points to a larger world, era, or body of work. "
                        "Ask what comes after this chapter if needed. Do not default to descriptive analysis language. Keep it to 1-3 sparse lowercase sentences."
                    ),
                    "rhone": (
                        "You are writing Sylvia Rhone's approved-track note. "
                        "Judge whether the approved song feels rooted and culturally honest, and ask the question that matters most about the first real audience or any compromise pressure. "
                        "Keep it to 2-3 warm lowercase sentences. Avoid generic genre-summary language."
                    ),
                    "rubin": (
                        "You are writing Rick Rubin's approved-track production-truth note. "
                        "Comment on the essential center of the approved track and what, if anything, still gets in its way. "
                        "Do not talk like a producer's checklist or analyzer readout. Ask or observe. Keep it to 1-3 sparse lowercase sentences."
                    ),
                },
            )
        except (AgentVoiceError, httpx.HTTPError) as exc:
            raise PipelineError(f"Approved-track voice generation failed: {exc}") from exc
        manager_message = generated["manager"]
        creative_message = generated["creative_director"]
        pending_drafts = {
            "kallman": generated["kallman"],
            "janick": generated["janick"],
            "rhone": generated["rhone"],
            "rubin": generated["rubin"],
        }

        _insert_feedback(
            conn,
            track_id=track_id,
            project_id=project_id,
            agent="manager",
            intent="track_approved_notification",
            message=manager_message,
        )
        _insert_feedback(
            conn,
            track_id=track_id,
            project_id=project_id,
            agent="creative_director",
            intent="artwork_needed",
            message=creative_message,
        )
        for agent, draft in pending_drafts.items():
            _submit_pending_message(
                conn,
                track_id=track_id,
                agent=agent,
                draft=draft,
                context="track_approved",
            )

    def _message_context(self, conn: sqlite3.Connection, track_id: int) -> str:
        track = self._resolve_track(conn, {"track_id": track_id})
        recent_feedback = conn.execute(
            """SELECT agent, intent, message
               FROM feedback
               WHERE track_id = ?
               ORDER BY id DESC
               LIMIT 3""",
            (track_id,),
        ).fetchall()
        fragments = [
            f"track={_track_title(track)}",
            f"state={track['state']}",
        ]
        for row in recent_feedback:
            fragments.append(f"{row['agent']}:{row['intent']}={str(row['message'])[:160]}")
        return " | ".join(fragments)

    def _classify_artist_intent(
        self, message: str, context: str
    ) -> tuple[IntentType, float, dict[str, Any], str]:
        api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            raise PipelineError("OPENROUTER_API_KEY is required for artist intent parsing")
        parser = IntentParser(openrouter_api_key=api_key)

        # classify() and close() must share one event loop — the client's
        # connections are bound to whichever loop opened them, so a second
        # asyncio.run() call (a fresh loop) cannot close them and raises
        # "RuntimeError: Event loop is closed".
        async def _classify_and_close():
            try:
                return await parser.classify(message, context=context)
            finally:
                await parser.close()

        intent = asyncio.run(_classify_and_close())
        return intent.intent_type, intent.confidence, intent.extracted_data, intent.reasoning

    def process_new_track(self, payload: dict[str, Any]) -> dict[str, Any]:
        pipeline_started = perf_counter()
        timings_ms: dict[str, float] = {}
        with _db_conn(self.db_path) as conn:
            track = self._resolve_track(conn, payload)
            track_id = int(track["id"])
            state = str(track["state"]).upper()
            if state in {"VAULT", "RELEASED"}:
                return {
                    "event": "new_track_detected",
                    "handled": False,
                    "track_id": track_id,
                    "state": state,
                }

            with conn:
                intake = self._ensure_intake(conn, track)
                project_id = intake["project_id"]
                track = self._resolve_track(conn, {"track_id": track_id})
                if state == "DRAFT":
                    _insert_feedback(
                        conn,
                        track_id=track_id,
                        project_id=project_id,
                        agent="a_and_r",
                        intent="new_track_ack",
                        message="Got it. I am putting this into review now.",
                    )
                    _transition(
                        conn,
                        track_id=track_id,
                        from_state="DRAFT",
                        to_state="IN_REVIEW",
                        changed_by="a_and_r",
                        reason="New track detected - entering review",
                    )
                    state = "IN_REVIEW"
                track = self._resolve_track(conn, {"track_id": track_id})

            analysis_id = _latest_analysis_id(conn, track_id)
            analysis: AudioAnalysis | None = None
            post_analysis_actions: list[str] = []
            if analysis_id is None:
                file_path = str(track["file_path"])
                try:
                    stage_started = perf_counter()
                    analysis = analyze(
                        file_path,
                        self.db_path,
                        track_id=track_id,
                        model=os.environ.get("OPENROUTER_AUDIO_MODEL", DEFAULT_OPENROUTER_MODEL),
                    )
                    timings_ms["audio_analysis"] = round((perf_counter() - stage_started) * 1000, 1)
                except AnalyzerError as exc:
                    timings_ms["audio_analysis"] = round((perf_counter() - stage_started) * 1000, 1)
                    timings_ms["dispatcher_total"] = round(
                        (perf_counter() - pipeline_started) * 1000, 1
                    )
                    with conn:
                        conn.execute(
                            "DELETE FROM feedback WHERE track_id = ? AND intent = 'pipeline_error'",
                            (track_id,),
                        )
                        _insert_feedback(
                            conn,
                            track_id=track_id,
                            project_id=project_id,
                            agent="a_and_r",
                            intent="pipeline_error",
                            message=f"Pipeline stopped during audio analysis: {exc}",
                        )
                    logger.exception("Audio analysis failed for track %d", track_id)
                    return {
                        "event": "new_track_detected",
                        "handled": True,
                        "track_id": track_id,
                        "state": "IN_REVIEW",
                        "error": str(exc),
                        "timings_ms": timings_ms,
                    }
                analysis_id = _latest_analysis_id(conn, track_id)
                if analysis_id is None:
                    message = "Audio analysis completed without persisting an audio_analyses row"
                    with conn:
                        conn.execute(
                            "DELETE FROM feedback WHERE track_id = ? AND intent = 'pipeline_error'",
                            (track_id,),
                        )
                        _insert_feedback(
                            conn,
                            track_id=track_id,
                            project_id=project_id,
                            agent="a_and_r",
                            intent="pipeline_error",
                            message=f"Pipeline stopped during audio analysis: {message}",
                        )
                    logger.error("Audio analysis did not persist a row for track %d", track_id)
                    return {
                        "event": "new_track_detected",
                        "handled": True,
                        "track_id": track_id,
                        "state": "IN_REVIEW",
                        "error": message,
                    }

            with conn:
                current = conn.execute(
                    "SELECT state FROM tracks WHERE id = ?",
                    (track_id,),
                ).fetchone()
                current_state = str(current["state"]).upper() if current else state
                panel = self._ensure_panel_session(conn, track_id)
                if analysis is None and analysis_id is not None:
                    analysis = _latest_analysis(conn, track_id)

            # _write_post_analysis_actions triggers slow, external-API-calling
            # steps (segment analysis, feature extraction, embeddings, artwork)
            # that each open their own db connection to write results. Running
            # this while the transaction above is still open would deadlock
            # those connections against this one — same reason `analyze()`
            # above runs outside any open transaction.
            if analysis_id is not None:
                try:
                    post_analysis_actions = _write_post_analysis_actions(
                        conn,
                        db_path=self.db_path,
                        track=track,
                        project_id=project_id,
                        analysis=analysis,
                        timings_ms=timings_ms,
                    )
                except PipelineError as exc:
                    timings_ms["dispatcher_total"] = round(
                        (perf_counter() - pipeline_started) * 1000, 1
                    )
                    with conn:
                        conn.execute(
                            "DELETE FROM feedback WHERE track_id = ? AND intent = 'pipeline_error'",
                            (track_id,),
                        )
                        _insert_feedback(
                            conn,
                            track_id=track_id,
                            project_id=project_id,
                            agent="system",
                            intent="pipeline_error",
                            message=f"Pipeline stopped during post-analysis actions: {exc}",
                        )
                    logger.exception("Post-analysis actions failed for track %d", track_id)
                    return {
                        "event": "new_track_detected",
                        "handled": True,
                        "track_id": track_id,
                        "state": current_state,
                        "analysis_id": analysis_id,
                        "error": str(exc),
                        "timings_ms": timings_ms,
                    }

            with conn:
                if current_state == "IN_REVIEW":
                    _transition(
                        conn,
                        track_id=track_id,
                        from_state="IN_REVIEW",
                        to_state="FEEDBACK_GIVEN",
                        changed_by="a_and_r",
                        reason="Audio analysis complete - feedback generated",
                    )

            timings_ms["dispatcher_total"] = round((perf_counter() - pipeline_started) * 1000, 1)
            return {
                "event": "new_track_detected",
                "handled": True,
                "track_id": track_id,
                "state": "FEEDBACK_GIVEN",
                "analysis_id": analysis_id,
                "intake": intake,
                "panel": panel,
                "post_analysis_actions": post_analysis_actions,
                "timings_ms": timings_ms,
            }

    def process_track_approved(
        self,
        payload: dict[str, Any],
        *,
        event: str = "track_approved",
    ) -> dict[str, Any]:
        with _db_conn(self.db_path) as conn:
            track = self._resolve_track(conn, payload)
            track_id = int(track["id"])
            state = str(track["state"]).upper()
            if state in {"VAULT", "RELEASED"}:
                return {"event": event, "handled": False, "track_id": track_id, "state": state}

            with conn:
                project_id = self._project_id_for_track(conn, track)
                artist_message = str(payload.get("message") or "").strip()
                if artist_message:
                    _upsert_feedback_message(
                        conn,
                        track_id=track_id,
                        project_id=project_id,
                        agent=str(payload.get("agent") or "a_and_r"),
                        message=artist_message,
                        channel=str(payload.get("channel") or "desktop"),
                        direction="inbound",
                        intent="approval" if event == "artist_approves" else None,
                    )
                if state in {"DRAFT"}:
                    raise PipelineError("Cannot approve a track before review")
                if state in {"IN_REVIEW", "FEEDBACK_GIVEN"}:
                    _transition(
                        conn,
                        track_id=track_id,
                        from_state=state,
                        to_state="APPROVED",
                        changed_by=payload.get("agent", "a_and_r"),
                        reason="A&R approved track",
                    )
                    state = "APPROVED"
                if state == "APPROVED":
                    _transition(
                        conn,
                        track_id=track_id,
                        from_state="APPROVED",
                        to_state="ART_NEEDED",
                        changed_by="system",
                        reason="Automatic: approved track needs artwork",
                    )
                    state = "ART_NEEDED"
                self._notify_track_approved_agents(conn, track_id, project_id=project_id)
                # Maren generates cover-art variants in the background. Slow
                # (~60s for 4 NanoBanana calls) — fire-and-forget so the
                # dispatcher response is immediate.
                if state == "ART_NEEDED":
                    db_path = self.db_path

                    def _maren_bg() -> None:
                        bg_conn = _connect(db_path)
                        try:
                            _trigger_maren_artwork(
                                bg_conn,
                                db_path=db_path,
                                track_id=track_id,
                            )
                        finally:
                            bg_conn.close()

                    threading.Thread(
                        target=_maren_bg,
                        name=f"maren-artwork-{track_id}",
                        daemon=True,
                    ).start()

            return {
                "event": event,
                "handled": True,
                "track_id": track_id,
                "state": state,
            }

    def process_artist_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        with _db_conn(self.db_path) as conn:
            track_id = payload.get("track_id")
            if track_id is None:
                raise PipelineError("artist_message_inbound requires track_id")
            track = self._resolve_track(conn, {"track_id": track_id})
            context = self._message_context(conn, int(track["id"]))
            project_id = self._project_id_for_track(conn, track)
            prior_addressed_agents = _latest_artist_named_agents(conn, int(track["id"]))

        message = str(payload.get("message") or "").strip()
        if not message:
            raise PipelineError("artist_message_inbound requires a non-empty message")

        intent_type, confidence, extracted_data, reasoning = self._classify_artist_intent(
            message, context
        )
        normalized_intent = _normalize_feedback_intent(intent_type.value)
        addressed_agents = _addressed_response_agents(message, prior_addressed_agents)
        with _db_conn(self.db_path) as conn, conn:
            feedback_id = _upsert_feedback_message(
                conn,
                track_id=int(track["id"]),
                project_id=project_id,
                agent=str(payload.get("agent") or "a_and_r"),
                message=message,
                channel=str(payload.get("channel") or "desktop"),
                direction="inbound",
                intent=normalized_intent,
            )

        if intent_type == IntentType.APPROVE:
            result = self.process_track_approved(payload, event="artist_approves")
            result["intent"] = intent_type.value
            result["confidence"] = confidence
            result["reasoning"] = reasoning
            result["feedback_id"] = feedback_id
            return result

        with _db_conn(self.db_path) as conn, conn:
            track = self._resolve_track(conn, {"track_id": track_id})
            track_id = int(track["id"])
            response_agents: list[str] = []
            if addressed_agents and intent_type not in {IntentType.REVISE, IntentType.DELAY}:
                response_agents = addressed_agents
            elif intent_type == IntentType.REVISE:
                _submit_pending_message(
                    conn,
                    track_id=track_id,
                    agent="a_and_r",
                    draft=_artist_revision_ack_message(),
                    context="artist_revision_pending",
                )
            elif intent_type == IntentType.DELAY:
                _submit_pending_message(
                    conn,
                    track_id=track_id,
                    agent="manager",
                    draft=_artist_delay_ack_message(extracted_data.get("date")),
                    context=f"artist_delay:{extracted_data.get('date', 'unspecified')}",
                )
            elif intent_type == IntentType.QUESTION:
                response_agents = ROUNDTABLE_AGENTS
            elif intent_type == IntentType.CASUAL:
                response_agents = ["creative_director", "manager"]
            else:
                response_agents = ["a_and_r", "creative_director", "manager"]

            prompt_context = _track_prompt_context(
                conn,
                track=track,
                project_id=project_id,
                analysis=_latest_analysis(conn, track_id),
                stage="artist_roundtable_reply",
            )

        response_ids: list[int] = []
        if response_agents:
            try:
                round_results = _run_roundtable_round(
                    db_path=self.db_path,
                    track_id=track_id,
                    project_id=project_id,
                    prompt_context=prompt_context,
                    trigger_text=message,
                    stage_label=f"artist_{intent_type.value}",
                    candidate_agents=response_agents,
                    max_turns=len(response_agents),
                    allow_manager_summary=(
                        len(response_agents) > 1
                        and "manager" in response_agents
                        and intent_type != IntentType.CASUAL
                    ),
                    require_all_agents=bool(addressed_agents)
                    or intent_type == IntentType.QUESTION,
                    default_intent=f"artist_{intent_type.value}_response",
                )
            except (AgentVoiceError, httpx.HTTPError) as exc:
                raise PipelineError(f"Roundtable reply generation failed: {exc}") from exc
            response_ids = [
                int(result["feedback_id"])
                for result in round_results
                if result.get("feedback_id") is not None
            ]

        return {
            "event": "artist_message_inbound",
            "handled": True,
            "track_id": track_id,
            "feedback_id": feedback_id,
            "intent": intent_type.value,
            "confidence": confidence,
            "reasoning": reasoning,
            "response_ids": response_ids,
        }

    def process_debate_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Agent-to-agent debate the artist can listen in on and interrupt."""
        with _db_conn(self.db_path) as conn:
            track = self._resolve_track(conn, {"track_id": payload.get("track_id")})
            track_id = int(track["id"])
            project_id = self._project_id_for_track(conn, track)
            last_inbound = conn.execute(
                """SELECT message FROM feedback
                   WHERE track_id = ? AND direction = 'inbound' AND agent != 'system'
                   ORDER BY id DESC LIMIT 1""",
                (track_id,),
            ).fetchone()
            prompt_context = _track_prompt_context(
                conn,
                track=track,
                project_id=project_id,
                analysis=_latest_analysis(conn, track_id),
                stage="agents_only_debate",
            )

        client_seed = str(payload.get("seed") or "").strip()
        if client_seed:
            trigger_text = client_seed
        elif last_inbound is not None:
            trigger_text = (
                "The artist asked the room this, then went quiet to listen: "
                f"\"{last_inbound['message']}\" — hash it out between yourselves."
            )
        else:
            trigger_text = (
                "The artist is listening in silently. Pick up the room's most recent "
                "unresolved point about this track and argue it out."
            )

        try:
            round_results = _run_roundtable_round(
                db_path=self.db_path,
                track_id=track_id,
                project_id=project_id,
                prompt_context=prompt_context,
                trigger_text=trigger_text,
                stage_label="agents_only_debate",
                candidate_agents=ROUNDTABLE_AGENTS,
                max_turns=ROUND_MAX_TURNS,
                allow_manager_summary=False,
                default_intent="agent_debate",
                audience="room",
                min_turns=2,
            )
        except (AgentVoiceError, httpx.HTTPError) as exc:
            raise PipelineError(f"Debate generation failed: {exc}") from exc

        return {
            "event": "agent_debate_requested",
            "handled": True,
            "track_id": track_id,
            "response_ids": [
                int(result["feedback_id"])
                for result in round_results
                if result.get("feedback_id") is not None
            ],
        }

    def process_revision_uploaded(self, payload: dict[str, Any]) -> dict[str, Any]:
        with _db_conn(self.db_path) as conn:
            track = self._resolve_track(conn, payload)
            track_id = int(track["id"])
            state = str(track["state"]).upper()
            parent_track_id = (
                int(track["parent_track_id"]) if track["parent_track_id"] is not None else None
            )
            if state in {"VAULT", "RELEASED"}:
                return {
                    "event": "revision_uploaded",
                    "handled": False,
                    "track_id": track_id,
                    "state": state,
                }

            project_id = self._project_id_for_track(conn, track)
            artist_message = str(payload.get("message") or "").strip()
            with conn:
                if artist_message:
                    _upsert_feedback_message(
                        conn,
                        track_id=track_id,
                        project_id=project_id,
                        agent=str(payload.get("agent") or "a_and_r"),
                        message=artist_message,
                        channel=str(payload.get("channel") or "desktop"),
                        direction="inbound",
                        intent="revision",
                    )
                _insert_feedback(
                    conn,
                    track_id=track_id,
                    project_id=project_id,
                    agent="a_and_r",
                    intent="revision_ack",
                    message="Got the revision. I am putting this version back into review now.",
                )
                if state in {"FEEDBACK_GIVEN", "APPROVED", "ART_NEEDED"}:
                    _transition(
                        conn,
                        track_id=track_id,
                        from_state=state,
                        to_state="DRAFT",
                        changed_by=str(payload.get("agent") or "artist"),
                        reason="Revision uploaded - restarting review",
                    )
                    state = "DRAFT"

            if state == "DRAFT":
                result = self.process_new_track(
                    {
                        "track_id": track_id,
                        "file_path": str(track["file_path"]),
                        "version": track["version"],
                    }
                )
                result["event"] = "revision_uploaded"
                result["revision_of"] = parent_track_id
                return result

            return {
                "event": "revision_uploaded",
                "handled": True,
                "track_id": track_id,
                "state": state,
                "revision_of": parent_track_id,
            }

    def process_conductor_message_delivered(
        self,
        payload: dict[str, Any],
        *,
        event: str,
    ) -> dict[str, Any]:
        with _db_conn(self.db_path) as conn:
            if not _table_exists(conn, "pending_messages"):
                raise PipelineError("pending_messages table is required for conductor delivery")

            raw_message_id = payload.get("message_id", payload.get("pending_message_id"))
            try:
                message_id = int(raw_message_id)
            except (TypeError, ValueError) as exc:
                raise PipelineError("payload.message_id must be an integer") from exc

            pending = conn.execute(
                "SELECT * FROM pending_messages WHERE id = ?",
                (message_id,),
            ).fetchone()
            if pending is None:
                raise PipelineError(f"Pending message {message_id} not found")

            track_id = int(pending["track_id"]) if pending["track_id"] is not None else None
            track = (
                self._resolve_track(conn, {"track_id": track_id}) if track_id is not None else None
            )
            project_id = self._project_id_for_track(conn, track)
            delivered_message = (
                str(payload.get("message") or "").strip()
                or str(pending["refined_draft"] or "").strip()
                or str(pending["draft"]).strip()
            )
            if not delivered_message:
                raise PipelineError(f"Pending message {message_id} has no deliverable draft")

            with conn:
                feedback_id = _upsert_feedback_message(
                    conn,
                    track_id=track_id,
                    project_id=project_id,
                    agent=str(pending["from_agent"]),
                    message=delivered_message,
                    channel=str(payload.get("channel") or "sms"),
                    direction="outbound",
                    intent=(
                        _normalize_feedback_intent(payload.get("intent"))
                        or _intent_from_pending_context(pending["context"])
                    ),
                )
                conn.execute(
                    """UPDATE pending_messages
                       SET status = 'approved',
                           refined_draft = COALESCE(?, refined_draft),
                           conductor_reasoning = COALESCE(?, conductor_reasoning),
                           sent_at = COALESCE(sent_at, datetime('now'))
                       WHERE id = ?""",
                    (
                        str(payload.get("refined_draft") or "").strip() or None,
                        str(payload.get("conductor_reasoning") or "").strip() or None,
                        message_id,
                    ),
                )

            return {
                "event": event,
                "handled": True,
                "message_id": message_id,
                "track_id": track_id,
                "feedback_id": feedback_id,
                "delivered_message": delivered_message,
            }

    def process_weekly_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        message = str(payload.get("message") or "").strip()
        if not message:
            raise PipelineError("weekly_summary_due requires payload.message")
        week_of = str(payload.get("week_of") or "").strip() or "unknown_week"
        with _db_conn(self.db_path) as conn, conn:
            _submit_pending_message(
                conn,
                track_id=payload.get("track_id"),
                agent="manager",
                draft=message,
                context=f"weekly_summary:{week_of}",
            )
        return {"event": "weekly_summary_due", "handled": True, "week_of": week_of}

    def process_timeout_feedback_stale(self, payload: dict[str, Any]) -> dict[str, Any]:
        with _db_conn(self.db_path) as conn, conn:
            track = self._resolve_track(conn, payload)
            _submit_pending_message(
                conn,
                track_id=int(track["id"]),
                agent="manager",
                draft=_timeout_feedback_stale_message(),
                context=f"timeout_feedback_stale:{payload.get('entered_at', '')}",
            )
        return {"event": "timeout_feedback_stale", "handled": True, "track_id": int(track["id"])}

    def process_timeout_art_overdue(self, payload: dict[str, Any]) -> dict[str, Any]:
        with _db_conn(self.db_path) as conn, conn:
            track = self._resolve_track(conn, payload)
            _submit_pending_message(
                conn,
                track_id=int(track["id"]),
                agent="creative_director",
                draft=_timeout_art_overdue_message(payload.get("release_date")),
                context=f"timeout_art_overdue:{payload.get('entered_at', '')}",
                priority="high",
            )
        return {"event": "timeout_art_overdue", "handled": True, "track_id": int(track["id"])}

    def process_timeout_release_date_missed(self, payload: dict[str, Any]) -> dict[str, Any]:
        with _db_conn(self.db_path) as conn, conn:
            track = self._resolve_track(conn, payload)
            _submit_pending_message(
                conn,
                track_id=int(track["id"]),
                agent="manager",
                draft=_timeout_release_missed_message(payload.get("release_date")),
                context=f"timeout_release_date_missed:{payload.get('release_date', '')}",
                priority="high",
            )
        return {
            "event": "timeout_release_date_missed",
            "handled": True,
            "track_id": int(track["id"]),
        }

    def process_catalog_memory_refresh(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Re-run cross-track pattern detection for a project's full catalog.

        Fired after multi-track album intake so patterns across all tracks are
        detected in one Gemini pass rather than track-by-track in isolation.
        `refresh_catalog_memory` skips re-storing any analysis rows — it only
        reads existing analyses and updates `audio_memory`.

        Payload keys:
            project_id (int): The project whose catalog to refresh.
        """
        import concurrent.futures  # noqa: PLC0415

        from audio_analysis.memory_builder import refresh_catalog_memory  # noqa: PLC0415

        project_id = payload.get("project_id")
        if not project_id:
            raise PipelineError("catalog_memory_refresh payload missing project_id")

        with _db_conn(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT t.id AS track_id
                FROM tracks t
                JOIN audio_analyses aa ON aa.track_id = t.id
                WHERE t.project_id = ?
                GROUP BY t.id
                ORDER BY MAX(aa.id) ASC
                """,
                (int(project_id),),
            ).fetchall()

        if len(rows) < 2:
            logger.info(
                "catalog_memory_refresh: project %d has fewer than 2 analyzed tracks, skipping",
                project_id,
            )
            return {
                "event": "catalog_memory_refresh",
                "handled": True,
                "project_id": project_id,
                "entries_updated": 0,
                "skipped": "fewer than 2 analyzed tracks",
            }

        anchor_track_id = int(rows[-1]["track_id"])
        model = os.environ.get("OPENROUTER_AUDIO_MODEL", DEFAULT_OPENROUTER_MODEL)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                entries_updated = pool.submit(
                    asyncio.run,
                    refresh_catalog_memory(self.db_path, anchor_track_id, model=model),
                ).result()
        else:
            entries_updated = asyncio.run(
                refresh_catalog_memory(self.db_path, anchor_track_id, model=model)
            )

        logger.info(
            "catalog_memory_refresh: project %d refreshed, %d memory entries updated",
            project_id,
            entries_updated,
        )
        return {
            "event": "catalog_memory_refresh",
            "handled": True,
            "project_id": project_id,
            "anchor_track_id": anchor_track_id,
            "entries_updated": entries_updated,
        }
