"""Maren's artwork orchestrator.

End-to-end flow for cover art generation:

  1. Collect everything Maren needs to read for a track:
       lyrics, standout segments (visual_anchors), essence elements,
       and the roundtable observations.
  2. Send all of that, plus Maren's SOUL and her nano_banana skill file,
     to Claude on OpenRouter. Claude (as Maren) produces 4 variant prompts
     in a strict JSON shape, each anchored to song content and committed to
     a single domain lens.
  3. Fire all 4 prompts to NanoBanana Pro in parallel.
  4. Write a row to `artwork_generations` per variant with the saved image
     path and Maren's rationale.
  5. Post a single feedback message from Maren to the roundtable, labeled
     "Cover variants — \"<track title>\"", listing the four with their
     rationale and image URLs.

Errors per variant are isolated — if one of the four fails, the other three
still ship. The orchestrator returns the list of stored generation rows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

import httpx

from audio_analysis.gemini_client import _openrouter_key

from .nano_banana_client import (
    DEFAULT_MODEL as NANO_BANANA_DEFAULT_MODEL,
    GeneratedImage,
    NanoBananaError,
    generate as nano_banana_generate,
)

logger = logging.getLogger(__name__)

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
BRIEF_MODEL = "anthropic/claude-sonnet-4.5"

REPO_ROOT = Path(__file__).resolve().parents[1]
MAREN_SOUL_PATH = REPO_ROOT / "agents" / "creative_director" / "SOUL.md"
MAREN_SKILL_PATH = REPO_ROOT / "agents" / "creative_director" / "skills" / "nano_banana.md"

VALID_AXES = {"medium", "vantage", "era", "abstraction"}


class MarenOrchestrationError(Exception):
    """Raised when artwork orchestration fails for a recoverable reason."""


# ── Context collection ─────────────────────────────────────────────────────


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _track_row(conn: sqlite3.Connection, track_id: int) -> dict[str, Any]:
    row = conn.execute(
        """SELECT t.id, t.title, t.state,
                  ap.name AS artist
             FROM tracks t
             LEFT JOIN artist_profile ap ON ap.id = 1
            WHERE t.id = ?""",
        (track_id,),
    ).fetchone()
    if row is None:
        raise MarenOrchestrationError(f"Track {track_id} not found")
    return dict(row)


def _lyrics_text(conn: sqlite3.Connection, track_id: int) -> str:
    row = conn.execute(
        "SELECT lyrics_clean FROM track_lyrics WHERE track_id = ?",
        (track_id,),
    ).fetchone()
    return (row["lyrics_clean"] if row else None) or "(no lyrics extracted)"


def _standout_segments(conn: sqlite3.Connection, track_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT start_sec, end_sec, section_label, mood,
               standout_reason, visual_anchor
          FROM track_segments
         WHERE track_id = ? AND standout = 1
         ORDER BY start_sec ASC
        """,
        (track_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _essence_elements(conn: sqlite3.Connection, track_id: int) -> list[str]:
    """Rubin's essence elements. Empty only when the analysis hasn't run.
    Malformed stored JSON surfaces as an error — Maren must not improvise
    on top of corrupted data.
    """
    row = conn.execute(
        "SELECT essence_elements FROM stem_instrumental_analyses WHERE track_id = ?",
        (track_id,),
    ).fetchone()
    if row is None or not row["essence_elements"]:
        return []
    parsed = json.loads(row["essence_elements"])
    if not isinstance(parsed, list):
        raise MarenOrchestrationError(
            f"essence_elements for track {track_id} is not a JSON array: {parsed!r}"
        )
    return [str(item) for item in parsed]


def _agent_observations(conn: sqlite3.Connection, track_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT agent, message, created_at
          FROM feedback
         WHERE track_id = ?
           AND direction = 'outbound'
           AND agent != 'system'
         ORDER BY datetime(created_at) ASC, id ASC
        """,
        (track_id,),
    ).fetchall()
    return [dict(row) for row in rows]


# ── Brief generation (Maren composes 4 variant prompts) ────────────────────


def _build_brief_prompt(
    track: dict[str, Any],
    lyrics: str,
    segments: list[dict[str, Any]],
    essence: list[str],
    observations: list[dict[str, Any]],
) -> str:
    standouts_block = (
        "\n".join(
            f"  {seg['start_sec']:.1f}-{seg['end_sec']:.1f}s "
            f"({seg.get('section_label') or 'unlabeled'}): "
            f"{seg.get('standout_reason', '')} "
            f"[visual_anchor: {seg.get('visual_anchor') or '—'}]"
            for seg in segments
        )
        or "  (no standout segments)"
    )
    essence_block = "\n".join(f"  - {item}" for item in essence) or "  (none recorded)"
    obs_block = (
        "\n\n".join(f"[{o['agent']}] {o['message']}" for o in observations)
        or "(no observations yet)"
    )

    return f"""You are Maren Lusk, the label's Creative Director. The track has just been
approved by A&R and is in ART_NEEDED. Your job is to produce four variant
cover-art prompts ready to send to NanoBanana Pro.

You MUST follow the skill file rules in `agents/creative_director/skills/
nano_banana.md`: the thematic anchor rule (one concrete image pulled from the
materials below), the evidence requirement (every variant cites its source),
and the variant axis rule (all four share the same anchor, diverge on exactly
one axis). Banned starting points: mood words, palette descriptions, genre
labels. The anchor is a noun phrase pulled from the song or the agents.

--- TRACK ---
Title: "{track.get('title') or 'Untitled'}"
Artist: {track.get('artist') or 'Unknown'}

--- LYRICS ---
{lyrics}

--- STANDOUT MOMENTS (segment analysis) ---
{standouts_block}

--- ESSENCE ELEMENTS (Rubin's non-negotiables) ---
{essence_block}

--- ROUNDTABLE OBSERVATIONS ---
{obs_block}

---

Now produce a single JSON object — ONLY the JSON, no markdown fences, no
preamble. Schema:

{{
  "brief": "<2-3 sentences naming the anchor and what you're working with.
            The anchor is a noun phrase. Cite its source.>",
  "variant_axis": "medium" | "vantage" | "era" | "abstraction",
  "variants": [
    {{
      "prompt": "<the full NanoBanana prompt following the 5-slot shape>",
      "rationale": "<one sentence: the source of the anchor + what this
                     variant is doing with it>"
    }},
    ... (exactly 4 variants)
  ]
}}

Rules for the prompts:
- Each follows the 5-slot shape: subject+adjectives, action, location,
  composition, style/medium, plus any text in quotes.
- Each commits to one domain lens (documentary photo / editorial illustration
  / film still / studio product photo / painting / mixed-media collage).
- For album covers: 1:1 aspect ratio, request 4K. No text on the cover unless
  the song obviously calls for it.
- Anti-patterns: keyword lists, vague subjects, mood words as composition,
  negative instructions, contradictory specs.
"""


async def _generate_brief(prompt: str, api_key: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    system_prompt = _load_text(MAREN_SOUL_PATH) + "\n\n---\n\n" + _load_text(MAREN_SKILL_PATH)
    body = {
        "model": BRIEF_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.6,
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(OPENROUTER_CHAT_URL, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise MarenOrchestrationError(f"Unexpected OpenRouter shape: {data}") from exc
    return _parse_brief_json(content)


def _parse_brief_json(content: str) -> dict[str, Any]:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            stripped = "\n".join(lines[1:-1]).strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise MarenOrchestrationError(
            f"Brief was not valid JSON: {exc}\n{content[:400]}"
        ) from exc
    if not isinstance(parsed, dict):
        raise MarenOrchestrationError("Brief is not a JSON object")

    brief = parsed.get("brief")
    if not isinstance(brief, str) or not brief.strip():
        raise MarenOrchestrationError("brief must be a non-empty string")

    axis = parsed.get("variant_axis")
    if axis not in VALID_AXES:
        raise MarenOrchestrationError(
            f"variant_axis must be one of {sorted(VALID_AXES)}, got {axis!r}"
        )

    variants = parsed.get("variants")
    if not isinstance(variants, list) or len(variants) != 4:
        raise MarenOrchestrationError("variants must be a list of exactly 4 items")
    for i, v in enumerate(variants):
        if not isinstance(v, dict):
            raise MarenOrchestrationError(f"variant {i} is not an object")
        if not isinstance(v.get("prompt"), str) or not v["prompt"].strip():
            raise MarenOrchestrationError(f"variant {i} missing prompt")
        if not isinstance(v.get("rationale"), str) or not v["rationale"].strip():
            raise MarenOrchestrationError(f"variant {i} missing rationale")

    return {
        "brief": brief.strip(),
        "variant_axis": axis,
        "variants": variants,
    }


# ── Image generation + storage ─────────────────────────────────────────────


def _artwork_dir(db_path: str, track_id: int) -> Path:
    data_dir = os.environ.get("AI_RECORD_LABEL_DATA")
    base = Path(data_dir) if data_dir else Path(db_path).resolve().parent
    out = base / "artwork" / f"track-{track_id}"
    out.mkdir(parents=True, exist_ok=True)
    return out


async def _generate_one(
    *,
    track_id: int,
    variant_index: int,
    prompt: str,
    output_dir: Path,
    nano_banana_model: str,
) -> GeneratedImage:
    label = f"track-{track_id}-v{variant_index + 1}"
    return await nano_banana_generate(
        prompt,
        output_dir=output_dir,
        label=label,
        model=nano_banana_model,
    )


def _insert_generation_row(
    conn: sqlite3.Connection,
    *,
    track_id: int,
    brief: str,
    prompt: str,
    variant_axis: str,
    rationale: str,
    model: str,
    image_path: Path | None,
) -> int:
    image_url = str(image_path) if image_path else None
    cur = conn.execute(
        """
        INSERT INTO artwork_generations
          (track_id, brief, prompt, variant_axis, rationale, model, image_url, picked)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (track_id, brief, prompt, variant_axis, rationale, model, image_url),
    )
    if cur.lastrowid is None:
        raise MarenOrchestrationError("INSERT did not return a lastrowid")
    return cur.lastrowid


def _post_roundtable_message(
    conn: sqlite3.Connection,
    *,
    track_id: int,
    track_title: str,
    rows: list[dict[str, Any]],
) -> None:
    """Drop a single Maren feedback message linking the variants."""
    successful = [r for r in rows if r.get("image_url")]
    if not successful:
        return
    body_lines = [f'Cover variants — "{track_title}"', ""]
    for i, row in enumerate(successful, start=1):
        body_lines.append(f"{i}. {row['rationale']}")
        body_lines.append(f"   → {row['image_url']}")
        body_lines.append("")
    message = "\n".join(body_lines).rstrip()
    conn.execute(
        """
        INSERT INTO feedback (track_id, agent, direction, intent, message, created_at)
        VALUES (?, 'creative_director', 'outbound', 'art_variants_proposed', ?, datetime('now'))
        """,
        (track_id, message),
    )


# ── Public entry point ─────────────────────────────────────────────────────


async def generate_artwork_variants(
    db_path: str,
    track_id: int,
    *,
    nano_banana_model: str = NANO_BANANA_DEFAULT_MODEL,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Generate 4 cover-art variants for a track. Returns the stored rows.

    Each row dict carries: id, track_id, brief, prompt, variant_axis, rationale,
    model, image_url (may be None if that variant failed).
    """
    key = _openrouter_key(api_key or os.environ.get("OPENROUTER_API_KEY"))
    if not key:
        raise MarenOrchestrationError("OPENROUTER_API_KEY is not set")

    # ── Collect context
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        track = _track_row(conn, track_id)
        lyrics = _lyrics_text(conn, track_id)
        segments = _standout_segments(conn, track_id)
        essence = _essence_elements(conn, track_id)
        observations = _agent_observations(conn, track_id)
    finally:
        conn.close()

    if not observations:
        raise MarenOrchestrationError(
            f"Track {track_id} has no roundtable observations — "
            "Maren can't generate artwork without a brief"
        )

    # ── Maren writes the brief + 4 variant prompts
    brief_prompt = _build_brief_prompt(track, lyrics, segments, essence, observations)
    brief_data = await _generate_brief(brief_prompt, key)
    brief_text = brief_data["brief"]
    variant_axis = brief_data["variant_axis"]
    variants = brief_data["variants"]
    logger.info(
        "Maren produced brief + %d variants on axis '%s' for track %d",
        len(variants),
        variant_axis,
        track_id,
    )

    # ── Fire all 4 to NanoBanana in parallel; failures isolated per variant
    output_dir = _artwork_dir(db_path, track_id)
    tasks = [
        _generate_one(
            track_id=track_id,
            variant_index=i,
            prompt=v["prompt"],
            output_dir=output_dir,
            nano_banana_model=nano_banana_model,
        )
        for i, v in enumerate(variants)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # ── Store rows (one per variant, even if image generation failed)
    stored_rows: list[dict[str, Any]] = []
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            for i, (variant, result) in enumerate(zip(variants, results, strict=True)):
                image_path: Path | None
                model_used = nano_banana_model
                if isinstance(result, GeneratedImage):
                    image_path = result.file_path
                    model_used = result.model
                else:
                    image_path = None
                    if isinstance(result, NanoBananaError):
                        logger.warning("Variant %d image gen failed: %s", i + 1, result)
                    elif isinstance(result, BaseException):
                        logger.exception("Variant %d errored", i + 1, exc_info=result)
                row_id = _insert_generation_row(
                    conn,
                    track_id=track_id,
                    brief=brief_text,
                    prompt=variant["prompt"],
                    variant_axis=variant_axis,
                    rationale=variant["rationale"],
                    model=model_used,
                    image_path=image_path,
                )
                stored = conn.execute(
                    "SELECT * FROM artwork_generations WHERE id = ?", (row_id,)
                ).fetchone()
                stored_rows.append(dict(stored))

            _post_roundtable_message(
                conn,
                track_id=track_id,
                track_title=track.get("title") or "Untitled",
                rows=stored_rows,
            )
    finally:
        conn.close()

    return stored_rows


def get_generations(db_path: str, track_id: int) -> list[dict[str, Any]]:
    """All artwork generation rows for a track, newest first."""
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, track_id, brief, prompt, variant_axis, rationale,
                   model, image_url, picked, created_at
              FROM artwork_generations
             WHERE track_id = ?
             ORDER BY created_at DESC, id DESC
            """,
            (track_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def pick_generation(db_path: str, generation_id: int) -> dict[str, Any]:
    """Mark a generation as picked. Clears prior pick on the same track."""
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT track_id, image_url FROM artwork_generations WHERE id = ?",
            (generation_id,),
        ).fetchone()
        if row is None:
            raise MarenOrchestrationError(f"Generation {generation_id} not found")
        if not row["image_url"]:
            raise MarenOrchestrationError(
                f"Generation {generation_id} has no image (gen failed); cannot pick"
            )
        with conn:
            conn.execute(
                """UPDATE artwork_generations
                      SET picked = 0
                    WHERE track_id = ?""",
                (row["track_id"],),
            )
            conn.execute(
                "UPDATE artwork_generations SET picked = 1 WHERE id = ?",
                (generation_id,),
            )
            picked_row = conn.execute(
                "SELECT * FROM artwork_generations WHERE id = ?",
                (generation_id,),
            ).fetchone()
        return dict(picked_row)
    finally:
        conn.close()
