"""Live coordination dispatcher for watcher/API events.

This is the concrete bridge between file intake and the release pipeline.  The
rules package defines what should happen; this module performs the database
updates and analysis work for the early track-review path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from audio_analysis.analyzer import AnalyzerError, analyze
from audio_analysis.gemini_client import DEFAULT_OPENROUTER_MODEL, _openrouter_key
from audio_analysis.models import AudioAnalysis
from coordination.intent_parser import IntentParser, IntentType
from stem_separation.separator import STEM_NAMES, StemSeparatorError, separate_stems
from audio_analysis.segment_analyzer import (
    SegmentAnalysisError,
    analyze_segments,
)
from audio_analysis.feature_extractor import (
    FeatureExtractionError,
    extract_audio_features,
)
from audio_analysis.embedding_extractor import (
    EmbeddingExtractionError,
    extract_embedding,
)
from artwork.maren_orchestrator import (
    MarenOrchestrationError,
    generate_artwork_variants,
)

logger = logging.getLogger(__name__)


REPO_ROOT = Path(__file__).resolve().parents[1]
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_AGENT_MODEL = "google/gemini-2.5-flash-lite"
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
        "You are writing Maren's artwork-gate message after track approval. "
        "Ground the visual direction in the music's mood, texture, and identity, connect it to catalog continuity when relevant, "
        "and ask for the right next art input. Be visually literate, not generic. "
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
    if os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]


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
) -> None:
    exists = conn.execute(
        "SELECT 1 FROM feedback WHERE track_id = ? AND agent = ? AND intent = ? LIMIT 1",
        (track_id, agent, intent),
    ).fetchone()
    if exists:
        return
    conn.execute(
        """INSERT INTO feedback
           (track_id, project_id, agent, message, channel, direction, intent)
           VALUES (?, ?, ?, ?, ?, 'outbound', ?)""",
        (track_id, project_id, agent, message, channel, intent),
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
                   intent = COALESCE(?, intent)
               WHERE id = ?""",
            (project_id, channel, intent, feedback_id),
        )
        return feedback_id

    cur = conn.execute(
        """INSERT INTO feedback
           (track_id, project_id, agent, message, channel, direction, intent)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (track_id, project_id, agent, message, channel, direction, intent),
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
) -> str:
    soul = _load_agent_soul(agent)
    research = _load_agent_research(agent)
    task = task_override or AGENT_TASKS.get(agent)
    if task is None:
        raise AgentVoiceError(f"No agent task prompt configured for {agent}")
    system_prompt = (
        "You are generating one outbound message for the AI Record Label app.\n"
        "Stay fully in character according to the soul document and any attached professional research profile.\n"
        "Write like a real message from that person, not an analyst summary or app caption.\n"
        "Use only the provided context. Do not invent moments, transitions, motives, or facts.\n"
        "Do not invent deadlines, dates, release targets, visual references, or approvals that are not explicitly supported by the context.\n"
        "Do not repeat the context blob back in generic terms. Interpret it through the agent's taste and role.\n"
        "Only mention facts like loop-based structure, minimal variation, flat energy, or timestamps when they are truly central to the point the agent would naturally make.\n"
        "If you are not Ravi or Dez, avoid sounding like an analyzer. Turn facts into taste judgments, questions, or direction appropriate to the role.\n"
        "Do not use pipe-separated summaries. Do not sound like a report unless the task explicitly calls for a room summary.\n"
        "Do not mention being an AI, prompt, JSON, or analysis object.\n"
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
        "Return ONLY valid JSON with schema: {\"message\": \"...\"}.\n\n"
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
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
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
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.35,
                "max_tokens": 320,
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
    body = response.json()
    try:
        content = body["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            lines = content.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines).strip()
        parsed = json.loads(content)
        message = str(parsed["message"]).strip()
    except (KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
        snippet = str(body)[:500]
        raise AgentVoiceError(f"Invalid {agent} voice response payload: {snippet}") from exc
    if not message:
        raise AgentVoiceError(f"{agent} voice response was empty")
    return message


def _agent_model() -> str:
    return os.environ.get("OPENROUTER_AGENT_MODEL", DEFAULT_AGENT_MODEL).strip() or DEFAULT_AGENT_MODEL


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
    return {agent: message for agent, message in zip(agents, results, strict=True)}


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
        raise PipelineError(
            "track_segments table is missing — migration 013 has not been applied"
        )
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


def _write_post_analysis_actions(
    conn: sqlite3.Connection,
    *,
    db_path: str,
    track: sqlite3.Row,
    project_id: int | None,
    analysis: AudioAnalysis | None,
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
    if _trigger_stem_separation(
        conn,
        db_path=db_path,
        track_id=track_id,
        file_path=str(track["file_path"]),
    ):
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

    if _trigger_segment_analysis(
        conn,
        db_path=db_path,
        track_id=track_id,
        file_path=str(track["file_path"]),
    ):
        actions.append("analyze_segments")

    if _trigger_audio_features(
        conn,
        db_path=db_path,
        track_id=track_id,
        file_path=str(track["file_path"]),
    ):
        actions.append("extract_audio_features")

    if _trigger_embedding(
        conn,
        db_path=db_path,
        track_id=track_id,
        file_path=str(track["file_path"]),
    ):
        actions.append("extract_embedding")

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
    try:
        generated_messages = _generate_agent_message_bundle(
            agents=["kallman", "a_and_r", "janick", "rhone", "rubin", "manager"],
            prompt_context=prompt_context,
            task_overrides={
                "kallman": (
                    "You are writing Craig Kallman's immediate conviction read after the room has heard the track. "
                    "Still behave like the fast gut-check executive: one sharp observation about whether this feels inevitable, "
                    "distinctive, or too hedged. Do not drift into arrangement critique. Do not talk about worlds or eras. "
                    "Use conviction language like 'this has it', 'jury's out', 'not landing yet', or equivalent. "
                    "Example shape: 'jury's out. i need the thing that makes me want to run this back immediately.' "
                    "1-2 lowercase sentences."
                ),
                "janick": (
                    "You are writing John Janick's vision note after approval-level music feedback exists. "
                    "Ignore song quality. Ask whether this feels like the start of a world, era, or body of work, or whether it still feels like an isolated song. "
                    "Do not describe structure, bpm, or arrangement unless it directly reveals identity across the catalog. "
                    "Example shape: 'the song lands. the question is whether this belongs to a world yet or if it's still standing alone.' "
                    "Prefer one pointed question or one verdict. 1-3 sparse lowercase sentences."
                ),
                "rhone": (
                    "You are writing Sylvia Rhone's cultural-authenticity read after the room has heard the track. "
                    "Name whether this feels like it comes from somewhere real, whether the specificity is being protected, "
                    "and who would claim it first if the answer is knowable. Do not default to generic analyzer phrases. "
                    "Example shape: 'i can hear the atmosphere, but who is this really for first?' "
                    "Warm, direct, lowercase. 2-3 sentences."
                ),
                "rubin": (
                    "You are writing Rick Rubin's essential-truth note after the room has heard the track. "
                    "Do not give mix notes. Ask what the song is actually trying to say, identify the truest center if the context supports it, "
                    "and question whether anything is in the way. Avoid analyzer jargon unless you transform it into a deeper question. "
                    "Example shape: 'there's patience in this. what is the one thing underneath it that wants to be heard?' "
                    "Sparse, meditative, lowercase. 1-3 sentences."
                ),
                "manager": (
                    "You are writing Dez's review-round summary after the team listened. "
                    "State the room's real decision in plain language, then the exact next choice for the artist. "
                    "Do not invent dates or deadlines. Do not sound like a dashboard widget. 2-4 short sentences."
                )
            },
        )
    except (AgentVoiceError, httpx.HTTPError) as exc:
        raise PipelineError(f"Agent voice generation failed: {exc}") from exc

    with conn:
        for agent, intent, message in (
            ("kallman", "early_conviction_feedback", generated_messages["kallman"]),
            ("a_and_r", "analysis_feedback", generated_messages["a_and_r"]),
            ("janick", "vision_assessment", generated_messages["janick"]),
            ("rhone", "cultural_authenticity_read", generated_messages["rhone"]),
            ("rubin", "essential_question_review", generated_messages["rubin"]),
            ("manager", "review_round_summary", generated_messages["manager"]),
        ):
            _insert_feedback(
                conn,
                track_id=track_id,
                project_id=project_id,
                agent=agent,
                intent=intent,
                message=message,
            )
    actions.extend(["kallman_review", "a_and_r_review", "janick_review", "rhone_review", "rubin_review"])
    return actions


def _manager_intake_message(project_title: str, track_count: int) -> str:
    unit = "track" if track_count == 1 else "tracks"
    return (
        f"Intake is complete: {track_count} {unit} under {project_title}. "
        "A&R review is underway."
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
                conn.execute(
                    "SELECT COUNT(*) FROM listening_panel WHERE active = 1"
                ).fetchone()[0]
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
            fragments.append(
                f"{row['agent']}:{row['intent']}={str(row['message'])[:160]}"
            )
        return " | ".join(fragments)

    def _classify_artist_intent(self, message: str, context: str) -> tuple[IntentType, float, dict[str, Any], str]:
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
                    analysis = analyze(
                        file_path,
                        self.db_path,
                        track_id=track_id,
                        model=os.environ.get("OPENROUTER_AUDIO_MODEL", DEFAULT_OPENROUTER_MODEL),
                    )
                except AnalyzerError as exc:
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
                    )
                except PipelineError as exc:
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

            return {
                "event": "new_track_detected",
                "handled": True,
                "track_id": track_id,
                "state": "FEEDBACK_GIVEN",
                "analysis_id": analysis_id,
                "intake": intake,
                "panel": panel,
                "post_analysis_actions": post_analysis_actions,
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

        message = str(payload.get("message") or "").strip()
        if not message:
            raise PipelineError("artist_message_inbound requires a non-empty message")

        intent_type, confidence, extracted_data, reasoning = self._classify_artist_intent(message, context)
        normalized_intent = _normalize_feedback_intent(intent_type.value)
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
            if intent_type == IntentType.REVISE:
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
                _submit_pending_message(
                    conn,
                    track_id=track_id,
                    agent="a_and_r",
                    draft=_artist_question_ack_message(),
                    context="artist_question",
                )
            elif intent_type == IntentType.CASUAL:
                _submit_pending_message(
                    conn,
                    track_id=track_id,
                    agent="manager",
                    draft="Seen. Nothing is blocked on my side.",
                    context="artist_casual",
                )
            else:
                _submit_pending_message(
                    conn,
                    track_id=track_id,
                    agent="a_and_r",
                    draft=_artist_clarification_message(),
                    context="artist_clarification",
                    priority="high",
                )

        return {
            "event": "artist_message_inbound",
            "handled": True,
            "track_id": track_id,
            "feedback_id": feedback_id,
            "intent": intent_type.value,
            "confidence": confidence,
            "reasoning": reasoning,
        }

    def process_revision_uploaded(self, payload: dict[str, Any]) -> dict[str, Any]:
        with _db_conn(self.db_path) as conn:
            track = self._resolve_track(conn, payload)
            track_id = int(track["id"])
            state = str(track["state"]).upper()
            parent_track_id = (
                int(track["parent_track_id"])
                if track["parent_track_id"] is not None
                else None
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
            track = self._resolve_track(conn, {"track_id": track_id}) if track_id is not None else None
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
        from audio_analysis.memory_builder import refresh_catalog_memory  # noqa: PLC0415
        import concurrent.futures  # noqa: PLC0415

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
