"""Collaborative, model-aware planning for Higgsfield music-video generations."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from audio_analysis.gemini_client import _openrouter_key

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_PROMPT_MODEL = "openrouter/free"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        is not None
    )


def load_label_thoughts(db_path: str | Path, track_id: int) -> list[dict[str, str]]:
    """Collect attributed label/song evidence for the visual roundtable."""
    thoughts: list[dict[str, str]] = []
    with sqlite3.connect(Path(db_path).expanduser().resolve()) as conn:
        conn.row_factory = sqlite3.Row
        if _table_exists(conn, "feedback"):
            rows = conn.execute(
                """
                SELECT agent, message, intent FROM feedback
                 WHERE track_id = ? AND direction = 'outbound'
                   AND intent IN (
                       'early_conviction_feedback', 'analysis_feedback',
                       'vision_assessment', 'cultural_authenticity_read',
                       'essential_question_review', 'review_round_summary',
                       'artwork_needed'
                   )
                 ORDER BY created_at DESC LIMIT 30
                """,
                (track_id,),
            ).fetchall()
            thoughts.extend(
                {
                    "source": str(row["agent"]),
                    "kind": str(row["intent"] or "label_feedback"),
                    "thought": str(row["message"]),
                }
                for row in reversed(rows)
            )
        if _table_exists(conn, "audio_analyses"):
            row = conn.execute(
                """
                SELECT genre_tags, mood_tags, instruments, structure,
                       mix_observations, notable_moments
                  FROM audio_analyses WHERE track_id = ? ORDER BY id DESC LIMIT 1
                """,
                (track_id,),
            ).fetchone()
            if row:
                thoughts.append(
                    {
                        "source": "ravi_audio_analysis",
                        "kind": "song_evidence",
                        "thought": json.dumps(dict(row), ensure_ascii=False),
                    }
                )
        if _table_exists(conn, "track_segments"):
            rows = conn.execute(
                """
                SELECT section_label, start_sec, visual_anchor, standout_reason
                  FROM track_segments
                 WHERE track_id = ? AND standout = 1 AND visual_anchor IS NOT NULL
                 ORDER BY start_sec
                """,
                (track_id,),
            ).fetchall()
            thoughts.extend(
                {
                    "source": "song_analysis",
                    "kind": "visual_anchor",
                    "thought": (
                        f"{row['section_label'] or 'section'} at {float(row['start_sec']):.1f}s: "
                        f"{row['visual_anchor']}. {row['standout_reason'] or ''}"
                    ).strip(),
                }
                for row in rows
            )
        if _table_exists(conn, "stem_instrumental_analyses"):
            row = conn.execute(
                "SELECT essence_elements FROM stem_instrumental_analyses WHERE track_id = ?",
                (track_id,),
            ).fetchone()
            if row and row["essence_elements"]:
                thoughts.append(
                    {
                        "source": "rubin",
                        "kind": "essence_elements",
                        "thought": str(row["essence_elements"]),
                    }
                )
    if not thoughts:
        raise ValueError(f"No label or song-analysis thoughts exist for track {track_id}")
    return thoughts


def _model_summary(profile: dict[str, Any]) -> dict[str, Any]:
    model_id = str(profile.get("id") or "").strip()
    if not model_id:
        raise ValueError("Higgsfield models_get profile must contain an id")
    if profile.get("output_type") != "video":
        raise ValueError(f"Higgsfield model {model_id} is not a video model")
    return {
        "id": model_id,
        "name": profile.get("name"),
        "description": profile.get("description"),
        "aspect_ratios": profile.get("aspect_ratios") or [],
        "durations": profile.get("durations") or [],
        "duration_range": profile.get("duration_range"),
        "parameters": profile.get("parameters") or [],
        "medias": profile.get("medias") or [],
    }


def _validate_aspect_ratio(model: dict[str, Any], aspect_ratio: str) -> str:
    supported = [str(value) for value in model["aspect_ratios"]]
    if supported and aspect_ratio not in supported:
        raise ValueError(
            f"Higgsfield model {model['id']} does not support aspect ratio {aspect_ratio}; "
            f"choose one of {supported}"
        )
    return aspect_ratio


def _validate_model_params(model: dict[str, Any], params: dict[str, Any], shot: int) -> None:
    definitions = {str(item.get("name")): item for item in model["parameters"]}
    unknown = set(params) - set(definitions)
    if unknown:
        raise ValueError(f"Shot {shot} uses unsupported model parameters: {sorted(unknown)}")
    for name, value in params.items():
        definition = definitions[name]
        options = definition.get("options")
        if options and value not in options:
            raise ValueError(f"Shot {shot} parameter {name} must be one of {options}")
        if definition.get("type") == "bool" and not isinstance(value, bool):
            raise ValueError(f"Shot {shot} parameter {name} must be boolean")
        if definition.get("type") == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"Shot {shot} parameter {name} must be numeric")
            if definition.get("min") is not None and value < definition["min"]:
                raise ValueError(f"Shot {shot} parameter {name} is below the model minimum")
            if definition.get("max") is not None and value > definition["max"]:
                raise ValueError(f"Shot {shot} parameter {name} exceeds the model maximum")


def _openrouter_json(system: str, user: dict[str, Any], api_key: str | None) -> dict[str, Any]:
    key = _openrouter_key(api_key)
    model = os.environ.get("OPENROUTER_PROMPT_MODEL", DEFAULT_PROMPT_MODEL).strip()
    response = httpx.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.75,
        },
        timeout=120,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError("Prompt generator returned a non-object response")
    return result


def generate_concept_draft(
    user_direction: str,
    label_thoughts: list[dict[str, str]],
    model_profile: dict[str, Any],
    *,
    track_duration_seconds: float,
    aspect_ratio: str = "9:16",
    api_key: str | None = None,
) -> dict[str, Any]:
    """Generate three attributed treatments without approving or submitting jobs."""
    if not user_direction.strip():
        raise ValueError("The artist's visual direction is required")
    if not label_thoughts:
        raise ValueError("At least one attributed label thought is required")
    if track_duration_seconds <= 0:
        raise ValueError("track_duration_seconds must be positive")
    model = _model_summary(model_profile)
    aspect_ratio = _validate_aspect_ratio(model, aspect_ratio)
    generated = _openrouter_json(
        (
            "You are the visual-development editor for an artist-owned record label. "
            "Reconcile the artist's direction with attributed label observations; the "
            "artist wins on conflicts. Create exactly three genuinely different music-video "
            "treatments optimized for the supplied Higgsfield model constraints. Avoid generic "
            "mood-word lists. Every treatment needs a concrete subject, action, environment, "
            "camera language, lighting/material texture, continuity rules, and five key story "
            "beats. Return JSON: {creative_synthesis: string, candidates: [{id: 1..3, title, "
            "treatment, master_prompt, evidence_sources: [source names], anchor_beats: "
            "[{timeline_sec, image, purpose}]}]}. Do not claim approval or generation."
        ),
        {
            "artist_direction": user_direction.strip(),
            "label_thoughts": label_thoughts,
            "track_duration_seconds": track_duration_seconds,
            "aspect_ratio": aspect_ratio,
            "higgsfield_model": model,
        },
        api_key,
    )
    candidates = generated.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 3:
        raise ValueError("Prompt generator must return exactly three candidate treatments")
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict) or not str(candidate.get("master_prompt") or "").strip():
            raise ValueError(f"Candidate {index} has no master_prompt")
        candidate["id"] = index
        candidate["status"] = "candidate"
    return {
        "schema_version": 2,
        "status": "awaiting_artist_approval",
        "created_at": datetime.now(UTC).isoformat(),
        "artist_direction": user_direction.strip(),
        "label_thoughts": label_thoughts,
        "track_duration_seconds": track_duration_seconds,
        "aspect_ratio": aspect_ratio,
        "higgsfield_model": model,
        "creative_synthesis": str(generated.get("creative_synthesis") or ""),
        "candidates": candidates,
    }


def generate_label_video_thoughts(
    user_direction: str,
    track_evidence: list[dict[str, str]],
    *,
    api_key: str | None = None,
) -> list[dict[str, str]]:
    """Ask the label roundtable for attributed visual ideas grounded in track evidence."""
    if not track_evidence:
        raise ValueError("Track evidence is required before asking the visual roundtable")
    generated = _openrouter_json(
        (
            "You are facilitating a record-label visual roundtable. Return one concise, "
            "specific music-video direction from each named voice: maren, ravi, rubin, "
            "janick, rhone, kallman, dez. Maren owns visual grammar; Ravi identifies the "
            "emotional/structural arc; Rubin protects the song's essence; Janick offers an "
            "ambitious visual reference; Rhone checks cultural specificity/authenticity; "
            "Kallman gives the immediate market-facing image; Dez defines executable scope. "
            "Every idea must cite a concrete piece of supplied evidence and respect the "
            "artist direction. Return JSON {thoughts: [{source, kind: 'video_direction', "
            "thought, evidence}]}. Do not claim consensus or artist approval."
        ),
        {"artist_direction": user_direction, "track_evidence": track_evidence},
        api_key,
    )
    thoughts = generated.get("thoughts")
    expected = {"maren", "ravi", "rubin", "janick", "rhone", "kallman", "dez"}
    if not isinstance(thoughts, list):
        raise ValueError("Visual roundtable returned no thoughts list")
    by_source = {
        str(item.get("source") or "").strip().casefold(): item
        for item in thoughts
        if isinstance(item, dict)
    }
    if set(by_source) != expected:
        raise ValueError(f"Visual roundtable must return exactly {sorted(expected)}")
    return [
        {
            "source": source,
            "kind": "video_direction",
            "thought": str(by_source[source].get("thought") or "").strip(),
            "evidence": str(by_source[source].get("evidence") or "").strip(),
        }
        for source in ("maren", "ravi", "rubin", "janick", "rhone", "kallman", "dez")
    ]


def approve_and_expand(
    draft: dict[str, Any],
    candidate_id: int,
    *,
    edited_prompt: str | None = None,
    artist_approved: bool = False,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Record artist approval and expand the chosen/edited prompt to 20 key shots."""
    if not artist_approved:
        raise PermissionError("Artist approval is required before expanding the generation plan")
    candidates = draft.get("candidates") or []
    selected = next((item for item in candidates if int(item.get("id", 0)) == candidate_id), None)
    if selected is None:
        raise ValueError(f"Candidate {candidate_id} does not exist")
    final_prompt = (edited_prompt or str(selected.get("master_prompt") or "")).strip()
    if not final_prompt:
        raise ValueError("The approved prompt cannot be empty")
    model = _model_summary(dict(draft.get("higgsfield_model") or {}))
    generated = _openrouter_json(
        (
            "Turn the artist-approved music-video treatment into exactly 20 generation shots. "
            "Use the supplied Higgsfield model's actual durations, aspect ratios, media roles, "
            "and parameter names. Each shot prompt must specify subject action, environment, "
            "shot size/lens or camera move, lighting, temporal motion, and one continuity cue. "
            "Shots must vary narratively while preserving identity and art direction. Return "
            "JSON: {shots: [{index: 1..20, timeline_start_sec, duration_sec, purpose, prompt, "
            "continuity, model_params: {only supported parameter names}}]}. These are planned "
            "jobs, not completed videos."
        ),
        {
            "approved_master_prompt": final_prompt,
            "selected_treatment": selected,
            "artist_direction": draft.get("artist_direction"),
            "label_thoughts": draft.get("label_thoughts"),
            "track_duration_seconds": draft.get("track_duration_seconds"),
            "aspect_ratio": draft.get("aspect_ratio"),
            "higgsfield_model": model,
        },
        api_key,
    )
    shots = generated.get("shots")
    if not isinstance(shots, list) or len(shots) != 20:
        raise ValueError("Shot planner must return exactly 20 shots")
    track_duration = float(draft.get("track_duration_seconds") or 0)
    for index, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict) or not str(shot.get("prompt") or "").strip():
            raise ValueError(f"Shot {index} has no prompt")
        params = shot.get("model_params") or {}
        if not isinstance(params, dict):
            raise ValueError(f"Shot {index} model_params must be an object")
        _validate_model_params(model, params, index)
        duration = float(shot.get("duration_sec") or 0)
        start = float(shot.get("timeline_start_sec") or 0)
        if duration <= 0 or start < 0 or (track_duration and start > track_duration):
            raise ValueError(f"Shot {index} has an invalid timeline or duration")
        duration_definition = next(
            (item for item in model["parameters"] if item.get("name") == "duration"), None
        )
        if duration_definition:
            _validate_model_params(model, {"duration": duration}, index)
            params["duration"] = duration
        if any(item.get("name") == "generate_audio" for item in model["parameters"]):
            params["generate_audio"] = False
        shot["aspect_ratio"] = _validate_aspect_ratio(
            model, str(draft.get("aspect_ratio") or "9:16")
        )
        shot["model_params"] = params
        shot["index"] = index
        shot["status"] = "planned"
    return {
        **draft,
        "status": "approved_for_cost_estimate",
        "approved_at": datetime.now(UTC).isoformat(),
        "selected_candidate_id": candidate_id,
        "approved_prompt": final_prompt,
        "prompt_was_edited": edited_prompt is not None,
        "shots": shots,
    }


def build_plan(
    concept: str, *, duration_seconds: float, aspect_ratio: str = "9:16"
) -> list[dict[str, Any]]:
    """Build an unapproved 20-shot skeleton for offline planning only."""
    if not concept.strip():
        raise ValueError("A concrete visual concept is required")
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    spacing = duration_seconds / 20
    shot_types = (
        "establishing",
        "portrait",
        "detail",
        "movement",
        "performance",
        "environment",
        "reaction",
        "symbol",
        "transition",
        "wide",
        "close-up",
        "tracking",
        "stillness",
        "texture",
        "profile",
        "overhead",
        "silhouette",
        "reveal",
        "climax",
        "final image",
    )
    return [
        {
            "index": index,
            "status": "planned",
            "aspect_ratio": aspect_ratio,
            "timeline_start_sec": round((index - 1) * spacing, 3),
            "duration_sec": min(5.0, duration_seconds),
            "prompt": f"{concept.strip()}. {shot_type} key shot; preserve approved continuity.",
        }
        for index, shot_type in enumerate(shot_types, start=1)
    ]
