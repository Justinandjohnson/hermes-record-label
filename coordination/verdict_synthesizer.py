"""Verdict synthesizer — Dez closes the meeting.

Reads every agent observation on a track, sends them to Claude via OpenRouter
with the verdict prompt, and writes a structured `roundtable_verdicts` row.

The verdict is the single thing the user acts on. Each row carries:
  - recommendation: SHIP | REVISE | VAULT | MINE_FOR_LOOPS
  - headline: one short line for the table center
  - reasoning: 2-4 sentences in Dez's voice
  - next_action_kind: approve | request_revision | vault | wave_vault
  - next_action_payload: JSON details for the action (e.g. wave-vault segments)

A new verdict supersedes the old one in place (sets `superseded_at`); the
unique partial index on `(track_id) WHERE superseded_at IS NULL` enforces
exactly one active verdict per track.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

import httpx

from audio_analysis.gemini_client import _openrouter_key

logger = logging.getLogger(__name__)

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
VERDICT_MODEL = "qwen/qwen3.8-27b"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEZ_SOUL_PATH = REPO_ROOT / "agents" / "manager" / "SOUL.md"

VALID_RECOMMENDATIONS = {"SHIP", "REVISE", "VAULT", "MINE_FOR_LOOPS"}
VALID_NEXT_ACTIONS = {"approve", "request_revision", "vault", "wave_vault"}

# Recommendation → default next_action_kind. The model can override but only
# within the valid set; this gives it a sane prior.
RECOMMENDATION_DEFAULTS = {
    "SHIP": "approve",
    "REVISE": "request_revision",
    "VAULT": "vault",
    "MINE_FOR_LOOPS": "wave_vault",
}


class VerdictSynthesisError(Exception):
    """Raised when verdict synthesis fails for a recoverable reason."""


# ── Prompt construction ────────────────────────────────────────────────────


def _load_dez_soul() -> str:
    return DEZ_SOUL_PATH.read_text(encoding="utf-8")


def _collect_feedback(conn: sqlite3.Connection, track_id: int) -> list[dict[str, Any]]:
    """Outbound agent messages on this track, oldest first."""
    rows = conn.execute(
        """
        SELECT id, agent, intent, message, created_at
          FROM feedback
         WHERE track_id = ?
           AND direction = 'outbound'
         ORDER BY datetime(created_at) ASC, id ASC
        """,
        (track_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _track_summary(conn: sqlite3.Connection, track_id: int) -> dict[str, Any]:
    track = conn.execute(
        """SELECT t.id, t.title, t.state, t.file_path,
                  ap.name AS artist
             FROM tracks t
             LEFT JOIN artist_profile ap ON ap.id = 1
            WHERE t.id = ?""",
        (track_id,),
    ).fetchone()
    if track is None:
        raise VerdictSynthesisError(f"Track {track_id} not found")
    return dict(track)


def _segment_highlights(conn: sqlite3.Connection, track_id: int) -> list[dict[str, Any]]:
    """Only the standout segments — the moments the agents should be pointing at."""
    rows = conn.execute(
        """
        SELECT start_sec, end_sec, section_label, mood,
               standout_reason, visual_anchor
          FROM track_segments
         WHERE track_id = ?
           AND standout = 1
         ORDER BY start_sec ASC
        """,
        (track_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _essence_elements(conn: sqlite3.Connection, track_id: int) -> list[str]:
    """Rubin's essence elements for a track. Empty list only when the row
    doesn't exist yet (instrumental analysis hasn't run). Stored JSON that
    fails to parse is treated as corruption and surfaced — never silently
    swallowed.
    """
    row = conn.execute(
        "SELECT essence_elements FROM stem_instrumental_analyses WHERE track_id = ?",
        (track_id,),
    ).fetchone()
    if row is None or not row["essence_elements"]:
        return []
    parsed = json.loads(row["essence_elements"])
    if not isinstance(parsed, list):
        raise VerdictSynthesisError(
            f"essence_elements for track {track_id} is not a JSON array: {parsed!r}"
        )
    return [str(item) for item in parsed]


def _lyrics_text(conn: sqlite3.Connection, track_id: int) -> str | None:
    """Clean lyrics for a track, or None when no lyrics have been extracted."""
    row = conn.execute(
        "SELECT lyrics_clean FROM track_lyrics WHERE track_id = ?",
        (track_id,),
    ).fetchone()
    if row is None:
        return None
    return row["lyrics_clean"]


def _build_prompt(
    track: dict[str, Any],
    feedback: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    essence: list[str],
    lyrics: str | None,
) -> str:
    feedback_block = "\n\n".join(
        f"[{msg['agent']}] {msg['message']}" for msg in feedback
    ) or "(no agent observations yet)"

    segments_block = (
        "\n".join(
            f"  {seg['start_sec']:.1f}-{seg['end_sec']:.1f}s "
            f"({seg.get('section_label') or 'unlabeled'}): "
            f"{seg.get('standout_reason', '')} "
            f"[visual: {seg.get('visual_anchor', '—')}]"
            for seg in segments
        )
        or "  (no standout segments analyzed)"
    )

    essence_block = "\n".join(f"  - {item}" for item in essence) or "  (none)"

    lyrics_block = lyrics.strip() if lyrics else "(no lyrics extracted — track may be instrumental or vocals not yet transcribed)"

    return f"""You are Dez (the Manager / Conductor). The roundtable has finished. Your job
is to close the meeting with a single structured decision the user can act on.

Track: "{track.get('title') or 'Untitled'}" by {track.get('artist') or 'Unknown'}
Current state: {track.get('state')}

Lyrics (if any):
{lyrics_block}

Standout moments from segment analysis:
{segments_block}

Essence elements (Rubin's non-negotiables):
{essence_block}

What the agents said (in order):

{feedback_block}

---

Now produce a single JSON object — ONLY the JSON, no preamble, no markdown
fences. The schema is:

{{
  "recommendation": "SHIP" | "REVISE" | "VAULT" | "MINE_FOR_LOOPS",
  "headline": "<one short line, max 70 chars, your voice>",
  "reasoning": "<2-4 sentences in Dez's voice, plain language, no jargon>",
  "next_action_kind": "approve" | "request_revision" | "vault" | "wave_vault",
  "next_action_payload": {{ ... }}
}}

Rules:

- SHIP means the track is ready to move to artwork. Next action: "approve".
- REVISE means it can land but the artist needs another pass. Next action:
  "request_revision". Payload: {{"focus_areas": ["string", ...]}} — at least
  one concrete area the artist should focus on, drawn from the agent
  observations.
- VAULT means the track isn't landing as a whole and nothing in it should be
  kept. Next action: "vault". Payload: {{}}.
- MINE_FOR_LOOPS means the song doesn't ship, but one or more moments are
  gold and should go to the Wave Vault. Next action: "wave_vault". Payload:
  {{"segments": [{{"stem": "vocals"|"drums"|"bass"|"other"|"full",
                    "start_sec": <number|null>,
                    "end_sec": <number|null>,
                    "notes": "<why this is worth saving>"}}]}}
  At least one segment. Use null for start/end if the whole stem matters.

- The headline is your synthesis, not a paraphrase of one agent. Speak in
  Dez's voice: direct, low-volume, decisive.
- The reasoning names the strongest evidence from the observations. Quote a
  specific line or moment when you can.
- Do not hedge. Pick one recommendation. The whole point of this step is
  conviction.
"""


# ── Model call ─────────────────────────────────────────────────────────────


async def _call_claude(prompt: str, api_key: str) -> str:
    """Send the verdict prompt to Claude via OpenRouter, return raw content."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": VERDICT_MODEL,
        "messages": [
            {"role": "system", "content": _load_dez_soul()},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(OPENROUTER_CHAT_URL, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise VerdictSynthesisError(f"Unexpected OpenRouter shape: {data}") from exc


# ── JSON parsing & validation ──────────────────────────────────────────────


def _parse_verdict_json(content: str) -> dict[str, Any]:
    """Tolerate accidental markdown fences but reject anything else."""
    stripped = content.strip()
    if stripped.startswith("```"):
        # strip first and last fence lines
        lines = stripped.splitlines()
        if len(lines) >= 2:
            stripped = "\n".join(lines[1:-1]).strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise VerdictSynthesisError(f"Verdict was not valid JSON: {exc}\n{content}") from exc

    if not isinstance(parsed, dict):
        raise VerdictSynthesisError(f"Verdict was not a JSON object: {parsed!r}")
    return parsed


def _validate_verdict(parsed: dict[str, Any]) -> dict[str, Any]:
    recommendation = parsed.get("recommendation")
    if recommendation not in VALID_RECOMMENDATIONS:
        raise VerdictSynthesisError(
            f"Invalid recommendation: {recommendation!r}. "
            f"Expected one of {sorted(VALID_RECOMMENDATIONS)}."
        )

    headline = parsed.get("headline")
    if not isinstance(headline, str) or not headline.strip():
        raise VerdictSynthesisError("headline must be a non-empty string")

    reasoning = parsed.get("reasoning")
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise VerdictSynthesisError("reasoning must be a non-empty string")

    next_action_kind = parsed.get("next_action_kind") or RECOMMENDATION_DEFAULTS[recommendation]
    if next_action_kind not in VALID_NEXT_ACTIONS:
        raise VerdictSynthesisError(
            f"Invalid next_action_kind: {next_action_kind!r}. "
            f"Expected one of {sorted(VALID_NEXT_ACTIONS)}."
        )

    next_action_payload = parsed.get("next_action_payload") or {}
    if not isinstance(next_action_payload, dict):
        raise VerdictSynthesisError("next_action_payload must be a JSON object")

    # Payload shape checks specific to action kind
    if next_action_kind == "wave_vault":
        segments = next_action_payload.get("segments")
        if not isinstance(segments, list) or not segments:
            raise VerdictSynthesisError(
                "wave_vault next_action_payload must have a non-empty 'segments' list"
            )
        for seg in segments:
            if not isinstance(seg, dict):
                raise VerdictSynthesisError("each wave_vault segment must be an object")
            if seg.get("stem") not in {"vocals", "drums", "bass", "other", "full"}:
                raise VerdictSynthesisError(
                    f"invalid stem in wave_vault segment: {seg.get('stem')!r}"
                )
    elif next_action_kind == "request_revision":
        focus = next_action_payload.get("focus_areas")
        if not isinstance(focus, list) or not focus:
            raise VerdictSynthesisError(
                "request_revision next_action_payload must have a non-empty 'focus_areas' list"
            )

    return {
        "recommendation": recommendation,
        "headline": headline.strip(),
        "reasoning": reasoning.strip(),
        "next_action_kind": next_action_kind,
        "next_action_payload": next_action_payload,
    }


# ── DB write ───────────────────────────────────────────────────────────────


def _supersede_active_verdict(conn: sqlite3.Connection, track_id: int) -> None:
    conn.execute(
        """
        UPDATE roundtable_verdicts
           SET superseded_at = CURRENT_TIMESTAMP
         WHERE track_id = ? AND superseded_at IS NULL
        """,
        (track_id,),
    )


def _insert_verdict(
    conn: sqlite3.Connection,
    track_id: int,
    verdict: dict[str, Any],
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO roundtable_verdicts (
            track_id, recommendation, headline, reasoning,
            next_action_kind, next_action_payload
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            track_id,
            verdict["recommendation"],
            verdict["headline"],
            verdict["reasoning"],
            verdict["next_action_kind"],
            json.dumps(verdict["next_action_payload"]),
        ),
    )
    if cursor.lastrowid is None:
        raise VerdictSynthesisError("INSERT did not return a lastrowid")
    return cursor.lastrowid


# ── Public entry point ─────────────────────────────────────────────────────


async def synthesize_verdict(
    db_path: str,
    track_id: int,
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Produce and store a verdict for a track. Returns the stored row as a dict.

    Raises VerdictSynthesisError if anything in the chain fails (no row written).
    """
    key = _openrouter_key(api_key or os.environ.get("OPENROUTER_API_KEY"))
    if not key:
        raise VerdictSynthesisError("OPENROUTER_API_KEY is not set")

    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        track = _track_summary(conn, track_id)
        feedback = _collect_feedback(conn, track_id)
        if not feedback:
            raise VerdictSynthesisError(
                f"Track {track_id} has no agent observations to synthesize from"
            )
        segments = _segment_highlights(conn, track_id)
        essence = _essence_elements(conn, track_id)
        lyrics = _lyrics_text(conn, track_id)

        prompt = _build_prompt(track, feedback, segments, essence, lyrics)
        raw = await _call_claude(prompt, key)
        parsed = _parse_verdict_json(raw)
        verdict = _validate_verdict(parsed)

        _supersede_active_verdict(conn, track_id)
        verdict_id = _insert_verdict(conn, track_id, verdict)
        conn.commit()

        row = conn.execute(
            "SELECT * FROM roundtable_verdicts WHERE id = ?", (verdict_id,)
        ).fetchone()
        result = dict(row)
        result["next_action_payload"] = (
            json.loads(result["next_action_payload"])
            if result.get("next_action_payload")
            else None
        )
        return result
    finally:
        conn.close()


def get_active_verdict(db_path: str, track_id: int) -> dict[str, Any] | None:
    """Return the current (non-superseded) verdict for a track, or None."""
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT * FROM roundtable_verdicts
             WHERE track_id = ? AND superseded_at IS NULL
            """,
            (track_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["next_action_payload"] = (
            json.loads(result["next_action_payload"])
            if result.get("next_action_payload")
            else None
        )
        return result
    finally:
        conn.close()
