"""Granular segment analysis — code-detected boundaries, Gemini-described content.

Architecture (three hard steps, no overlap):

  1. librosa detects structural boundaries deterministically from MFCC, chroma,
     and RMS novelty functions. No model is involved. Boundaries are facts
     computed from the signal and enforced to respect min/max duration limits.

  2. Gemini receives the full audio AND the fixed boundary list. Its only job
     is to describe what it hears within each pre-defined window:
     section_label, energy, elements, mood, production_notes, standout,
     visual_anchor. It does NOT move the boundaries.

  3. Python validates the response against the canonical boundaries, enforces
     that every window was described, and overrides start_sec/end_sec with
     the values from step 1 (ignoring any rounding Gemini may introduce).

This eliminates the root cause of uniform-slice hallucination: previously
Gemini was asked to both detect AND describe structure in one pass; it guessed
wrong on boundaries and stopped early. Now boundaries are ground truth from
the signal; Gemini only adds meaning to them.

Stored in the `track_segments` table created in migration 013.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import httpx
import librosa
import numpy as np
import soundfile as sf

from .gemini_client import (
    DEFAULT_OPENROUTER_MODEL,
    GeminiClientError,
    SUPPORTED_FORMATS,
    _openrouter_key,
    validate_audio_file,
)

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Duration constraints for individual segments.
# The model call enforces these; no segment will ever be shorter/longer.
MIN_SEGMENT_DUR = 3.0   # seconds — shorter fragments aren't musically useful
MAX_SEGMENT_DUR = 45.0  # seconds — longer sections get split at midpoint

# If librosa detects no peaks (completely uniform audio), fall back to
# equal-duration chunks no longer than this value.
FALLBACK_CHUNK_DUR = 20.0

# Coverage guard: raise if detected segments span less than 95 % of the track.
MIN_COVERAGE_RATIO = 0.95

# Maximum segments per Gemini description call.  This model uses extended
# thinking which consumes output tokens for reasoning; at 12 segments Gemini
# consistently stops early (finish_reason=stop with partial output).  6 is
# the empirically confirmed reliable limit for this model.
DESCRIPTION_BATCH_SIZE = 6


class SegmentAnalysisError(Exception):
    """Raised when segment analysis fails for a recoverable reason."""


# ── Step 1: deterministic boundary detection ──────────────────────────────


def _detect_boundaries(path: Path) -> tuple[float, list[tuple[float, float]]]:
    """Compute structural segment boundaries from audio features.

    Uses MFCC, chroma, and RMS change rates to build a novelty function, then
    finds its peaks with librosa.util.peak_pick. Duration constraints
    (MIN_SEGMENT_DUR / MAX_SEGMENT_DUR) are enforced by merging and splitting.

    Returns:
        (total_duration_seconds, [(start_sec, end_sec), ...])
        Segments span [0, total_duration] with no gaps.
    """
    y, sr = librosa.load(str(path), sr=22050, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))

    if duration <= MIN_SEGMENT_DUR:
        return duration, [(0.0, round(duration, 3))]

    hop_length = 512
    frames_per_sec = sr / hop_length  # ≈ 43 frames/sec

    # ── Feature extraction ──────────────────────────────────────────────────

    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20, hop_length=hop_length)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]

    n_frames = min(mfcc.shape[1], chroma.shape[1], len(rms))

    # ── Novelty function ────────────────────────────────────────────────────
    # Each novelty signal = frame-to-frame L2 distance on normalised features.
    # All three signals are normalised to [0, 1] before combining so no single
    # feature dominates.

    def _frame_novelty(feat: np.ndarray) -> np.ndarray:
        """L2 norm of consecutive-frame difference on zero-variance-clipped features."""
        std = feat.std(axis=1, keepdims=True)
        std[std < 1e-8] = 1.0
        normed = feat / std
        return np.sqrt(np.sum(np.diff(normed, axis=1) ** 2, axis=0))

    mfcc_nov = _frame_novelty(mfcc[:, :n_frames].astype(float))
    chroma_nov = _frame_novelty(chroma[:, :n_frames].astype(float))
    rms_nov = np.abs(np.diff(rms[:n_frames].astype(float)))

    n_diff = min(len(mfcc_nov), len(chroma_nov), len(rms_nov))

    def _norm(v: np.ndarray) -> np.ndarray:
        peak = v.max()
        return v / peak if peak > 0 else v

    novelty: np.ndarray = (
        _norm(mfcc_nov[:n_diff])
        + _norm(chroma_nov[:n_diff])
        + _norm(rms_nov[:n_diff])
    )

    # ── Peak picking ────────────────────────────────────────────────────────

    min_frames = max(1, int(MIN_SEGMENT_DUR * frames_per_sec))
    avg_win = min_frames

    try:
        boundary_frames: np.ndarray = librosa.util.peak_pick(
            novelty,
            pre_max=min_frames,
            post_max=min_frames,
            pre_avg=avg_win,
            post_avg=avg_win,
            delta=0.05,
            wait=min_frames,
        )
    except Exception as exc:
        raise SegmentAnalysisError(f"librosa peak_pick failed: {exc}") from exc

    # ── Build boundary time list ────────────────────────────────────────────

    if len(boundary_frames) == 0:
        # Uniform audio — fall back to equal-duration chunks
        n_chunks = max(1, int(np.ceil(duration / FALLBACK_CHUNK_DUR)))
        chunk = duration / n_chunks
        pairs: list[tuple[float, float]] = [
            (round(i * chunk, 3), round(min((i + 1) * chunk, duration), 3))
            for i in range(n_chunks)
        ]
        return duration, pairs

    boundary_secs = sorted(
        {0.0}
        | {
            round(float(librosa.frames_to_time(int(f), sr=sr, hop_length=hop_length)), 3)
            for f in boundary_frames
        }
        | {round(duration, 3)}
    )

    pairs = list(zip(boundary_secs[:-1], boundary_secs[1:]))

    # ── Apply duration constraints ──────────────────────────────────────────

    pairs = _merge_short_segments(pairs)
    pairs = _split_long_segments(pairs)

    return duration, pairs


def _merge_short_segments(
    pairs: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Merge segments shorter than MIN_SEGMENT_DUR into their neighbor."""
    if not pairs:
        return pairs
    result = list(pairs)
    i = 0
    while i < len(result):
        start, end = result[i]
        if end - start < MIN_SEGMENT_DUR and len(result) > 1:
            if i == len(result) - 1:
                prev_start, _ = result[i - 1]
                result[i - 1] = (prev_start, end)
                result.pop(i)
            else:
                _, next_end = result[i + 1]
                result[i] = (start, next_end)
                result.pop(i + 1)
        else:
            i += 1
    return result


def _split_long_segments(
    pairs: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Split segments longer than MAX_SEGMENT_DUR at the midpoint (recursive)."""
    result: list[tuple[float, float]] = []
    for start, end in pairs:
        if end - start > MAX_SEGMENT_DUR:
            mid = round((start + end) / 2.0, 3)
            result.extend(_split_long_segments([(start, mid), (mid, end)]))
        else:
            result.append((start, end))
    return result


# ── Step 2: Gemini description call ───────────────────────────────────────


def _build_description_prompt(boundaries: list[tuple[float, float]]) -> str:
    """Build the Gemini prompt that asks only for per-segment descriptions."""
    boundary_lines = "\n".join(
        f"  {i + 1}. {start:.3f}s – {end:.3f}s  (duration: {end - start:.1f}s)"
        for i, (start, end) in enumerate(boundaries)
    )
    n = len(boundaries)
    return f"""\
You are listening to a music track. The structural segment boundaries have already
been computed from audio feature analysis. Your job is to describe what you ACTUALLY
HEAR within each pre-defined time window — do NOT move or change the boundaries.

CRITICAL — two rules that must both be satisfied:

1. DESCRIBE WHAT YOU ACTUALLY HEAR IN DETAIL. For every segment, write a specific,
   rich description of the real sonic content: which instruments are present, what
   rhythm patterns are running, what melodies or chords you hear, what textures and
   timbres stand out. Be concrete — name the sounds, not just the vibe.

2. DO NOT INVENT VARIATION. If a segment sounds identical or nearly identical to a
   previous one, say so explicitly in production_notes (e.g. "same loop as above,
   repeating: kick on beats 1 and 3, snare on 2 and 4, detuned electric piano
   chord stabs on the off-beats, sub bass, vinyl crackle throughout"). Do NOT invent
   energy changes, drops, buildups, or structural shifts that aren't audibly present.
   Many tracks — especially electronic or loop-based music — repeat the same material
   for their entire runtime. That's fine. Describe what's there honestly AND note
   it's repeating.

The track has {n} segments:

{boundary_lines}

Return a JSON object with a single key "segments" whose value is an array of
exactly {n} objects, one per window, in the same order. Each object:

{{
  "start_sec": <copy the exact decimal value from the list above>,
  "end_sec": <copy the exact decimal value from the list above>,
  "section_label": "intro" | "verse" | "pre_chorus" | "chorus" | "drop" |
                   "bridge" | "breakdown" | "outro" | "loop" |
                   "<freeform if none fit>",
  "energy": <integer 1-10>,
  "elements_present": ["vocal lead", "sub bass", "kick", ...],
  "mood": "<one short descriptor: 'tense', 'yearning', 'still', etc.>",
  "production_notes": "<specific description of what you hear: instruments, rhythms,\
 melodies, textures, timbres — ALWAYS include this detail even for repeating segments;\
 if it repeats, say so AND describe what is repeating>",
  "standout": <true | false>,
  "standout_reason": "<why this moment earns the song if standout=true, else null>",
  "visual_anchor": "<one concrete image — an object, place, or action; not a mood word>"
}}

Rules:
- Describe ALL {n} segments. Skipping any segment will break the parser.
- elements_present must list at least one element per segment.
- standout=true should be rare — mark only moments that genuinely and audibly lift,
  drop, resolve, or transform the track (0–3 per track, often 0 for loop tracks).
  A segment that is simply the same loop continuing is NOT standout. But if the loop
  itself contains a particularly striking detail (an unusual chord, a memorable
  melodic hook, a distinctive rhythmic pattern), note it in production_notes — you
  don't need standout=true for that.
- visual_anchor must be concrete:
    GOOD: "a single brake light reflected on a wet highway at 2 a.m."
    BAD: "a sense of melancholy and late-night longing"
- Copy start_sec and end_sec exactly as written above.
- Use section_label "loop" for repeating loop sections that carry no structural role.

Return ONLY the JSON object. No markdown fences, no preamble, no commentary.
"""


async def _call_gemini_batch(
    slice_path: Path,
    batch: list[tuple[float, float]],
    *,
    model: str,
    api_key: str,
) -> str:
    """One Gemini call for a single batch of boundaries.

    slice_path is a WAV covering exactly the batch's time range.
    Returns the raw JSON string.
    """
    audio_b64 = base64.b64encode(slice_path.read_bytes()).decode("ascii")
    prompt = _build_description_prompt(batch)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "input_audio",
                        "input_audio": {"data": audio_b64, "format": "wav"},
                    },
                ],
            }
        ],
        "temperature": 0.2,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://ai-record-label.local",
        "X-Title": "AI Record Label",
    }
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(OPENROUTER_URL, headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPStatusError as exc:
        raise SegmentAnalysisError(
            f"OpenRouter HTTP {exc.response.status_code}: {exc.response.text[:400]}"
        ) from exc
    except httpx.HTTPError as exc:
        raise SegmentAnalysisError(f"OpenRouter request failed: {exc}") from exc

    try:
        raw_text: str = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise SegmentAnalysisError(
            f"Unexpected OpenRouter response shape: {body}"
        ) from exc
    if not raw_text:
        raise SegmentAnalysisError("OpenRouter returned empty content")
    return raw_text


async def _call_gemini_for_descriptions(
    path: Path,
    boundaries: list[tuple[float, float]],
    *,
    model: str,
    api_key: str,
) -> list[dict[str, Any]]:
    """Describe all boundaries via Gemini, one batch at a time.

    Each batch gets its own audio slice so Gemini only processes the portion it
    is describing.  This keeps each request small and within Gemini's reliable
    output range regardless of track length.

    Returns the full flat list of coerced segment dicts.
    """
    if path.suffix.lower() not in SUPPORTED_FORMATS:
        raise SegmentAnalysisError(f"Unsupported audio format: {path.suffix}")

    # Load full audio once; slice in-memory per batch
    y, sr = librosa.load(str(path), sr=22050, mono=True)

    batches: list[list[tuple[float, float]]] = [
        boundaries[i : i + DESCRIPTION_BATCH_SIZE]
        for i in range(0, len(boundaries), DESCRIPTION_BATCH_SIZE)
    ]

    logger.info(
        "Describing %d segments in %d batch(es) of up to %d",
        len(boundaries),
        len(batches),
        DESCRIPTION_BATCH_SIZE,
    )

    all_segments: list[dict[str, Any]] = []
    seg_offset = 0
    for batch_idx, batch in enumerate(batches):
        batch_start = batch[0][0]
        batch_end = batch[-1][1]
        logger.info(
            "Batch %d/%d: segments %d–%d (%.1fs–%.1fs)",
            batch_idx + 1,
            len(batches),
            seg_offset + 1,
            seg_offset + len(batch),
            batch_start,
            batch_end,
        )

        # Extract audio slice for this batch
        start_sample = int(batch_start * sr)
        end_sample = min(int(batch_end * sr), len(y))
        audio_slice = y[start_sample:end_sample]

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            sf.write(str(tmp_path), audio_slice, sr, subtype="PCM_16")
            raw = await _call_gemini_batch(
                tmp_path, batch, model=model, api_key=api_key
            )
        finally:
            tmp_path.unlink(missing_ok=True)

        try:
            batch_segments = _parse_described_segments(raw, batch)
        except SegmentAnalysisError as exc:
            if len(batch) == 1:
                raise
            logger.warning(
                "Gemini batch %d returned an unusable segment set (%s); "
                "retrying each boundary independently",
                batch_idx + 1,
                exc,
            )
            batch_segments = []
            for boundary in batch:
                segment_start, segment_end = boundary
                segment_audio = y[
                    int(segment_start * sr) : min(int(segment_end * sr), len(y))
                ]
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    segment_path = Path(tmp.name)
                try:
                    sf.write(str(segment_path), segment_audio, sr, subtype="PCM_16")
                    segment_raw = await _call_gemini_batch(
                        segment_path,
                        [boundary],
                        model=model,
                        api_key=api_key,
                    )
                    batch_segments.extend(
                        _parse_described_segments(segment_raw, [boundary])
                    )
                finally:
                    segment_path.unlink(missing_ok=True)
        all_segments.extend(batch_segments)
        seg_offset += len(batch)

    return all_segments


# ── Step 3: parse and validate ─────────────────────────────────────────────

# Tolerance for boundary echo validation: Gemini is expected to copy back the
# exact values we sent, but may introduce minor floating-point rounding.
_BOUNDARY_TOLERANCE_SEC = 1.0


def _coerce_segment(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate one segment dict, raise SegmentAnalysisError if invalid."""
    try:
        start = float(raw["start_sec"])
        end = float(raw["end_sec"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SegmentAnalysisError(
            f"segment missing/bad start_sec/end_sec: {raw}"
        ) from exc
    if end <= start:
        raise SegmentAnalysisError(f"segment end_sec must be > start_sec: {raw}")

    energy_raw = raw.get("energy")
    try:
        energy = int(energy_raw) if energy_raw is not None else None
    except (TypeError, ValueError) as exc:
        raise SegmentAnalysisError(
            f"segment energy must be an integer: {raw}"
        ) from exc
    if energy is not None and not (1 <= energy <= 10):
        raise SegmentAnalysisError(f"segment energy out of range 1-10: {energy}")

    elements = raw.get("elements_present")
    if elements is None:
        raise SegmentAnalysisError(
            f"segment is missing elements_present (required field): {raw}"
        )
    if not isinstance(elements, list):
        raise SegmentAnalysisError(f"elements_present must be a list: {raw}")
    if not elements:
        raise SegmentAnalysisError(
            f"elements_present must list at least one element for segment "
            f"{raw.get('start_sec')}-{raw.get('end_sec')}s"
        )

    standout = bool(raw.get("standout"))
    standout_reason = raw.get("standout_reason")
    if standout and not standout_reason:
        raise SegmentAnalysisError(
            "standout=true segments must include a standout_reason"
        )

    visual_anchor = raw.get("visual_anchor")
    if visual_anchor is not None and not isinstance(visual_anchor, str):
        raise SegmentAnalysisError("visual_anchor must be a string")

    return {
        "start_sec": start,
        "end_sec": end,
        "section_label": raw.get("section_label"),
        "energy": energy,
        "elements_present": elements,
        "mood": raw.get("mood"),
        "production_notes": raw.get("production_notes"),
        "standout": 1 if standout else 0,
        "standout_reason": standout_reason if standout else None,
        "visual_anchor": visual_anchor,
    }


def _parse_described_segments(
    raw_text: str,
    expected_boundaries: list[tuple[float, float]],
) -> list[dict[str, Any]]:
    """Parse and validate Gemini's per-segment descriptions.

    Enforces:
    - Valid JSON with a "segments" array
    - Exactly len(expected_boundaries) segments
    - Each segment's start_sec / end_sec within _BOUNDARY_TOLERANCE_SEC of
      the canonical values (then overwritten with canonical values)
    - All required fields present and valid (via _coerce_segment)
    """
    stripped = raw_text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            stripped = "\n".join(lines[1:-1]).strip()

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise SegmentAnalysisError(
            f"Gemini response not valid JSON: {exc}\n{raw_text[:500]}"
        ) from exc

    if not isinstance(parsed, dict) or "segments" not in parsed:
        raise SegmentAnalysisError(
            "Expected JSON object with a 'segments' key"
        )

    segs_raw = parsed["segments"]
    if not isinstance(segs_raw, list):
        raise SegmentAnalysisError("'segments' must be a JSON array")

    n_expected = len(expected_boundaries)
    if len(segs_raw) != n_expected:
        raise SegmentAnalysisError(
            f"Expected {n_expected} segment descriptions, "
            f"Gemini returned {len(segs_raw)}"
        )

    result: list[dict[str, Any]] = []
    for i, (raw_seg, (exp_start, exp_end)) in enumerate(
        zip(segs_raw, expected_boundaries)
    ):
        # Validate boundary echo
        try:
            got_start = float(raw_seg.get("start_sec", exp_start))
            got_end = float(raw_seg.get("end_sec", exp_end))
        except (TypeError, ValueError) as exc:
            raise SegmentAnalysisError(
                f"segment {i}: non-numeric start/end in Gemini response"
            ) from exc

        if (
            abs(got_start - exp_start) > _BOUNDARY_TOLERANCE_SEC
            or abs(got_end - exp_end) > _BOUNDARY_TOLERANCE_SEC
        ):
            raise SegmentAnalysisError(
                f"segment {i}: Gemini altered boundaries "
                f"(expected {exp_start:.3f}–{exp_end:.3f}s, "
                f"got {got_start:.3f}–{got_end:.3f}s)"
            )

        # Override with canonical values — Gemini's rounding is ignored
        raw_seg["start_sec"] = exp_start
        raw_seg["end_sec"] = exp_end

        result.append(_coerce_segment(raw_seg))

    return result


def _validate_coverage(
    segments: list[dict[str, Any]],
    total_duration: float,
) -> None:
    """Raise if segments don't cover at least MIN_COVERAGE_RATIO of the track."""
    if not segments:
        raise SegmentAnalysisError("No segments produced")

    first_start = segments[0]["start_sec"]
    last_end = segments[-1]["end_sec"]
    covered = last_end - first_start

    if first_start > 1.0:
        raise SegmentAnalysisError(
            f"First segment starts at {first_start:.1f}s — track start is uncovered"
        )
    if covered < total_duration * MIN_COVERAGE_RATIO:
        raise SegmentAnalysisError(
            f"Segments cover only {covered:.1f}s of {total_duration:.1f}s "
            f"({covered / total_duration * 100:.0f}% < "
            f"{MIN_COVERAGE_RATIO * 100:.0f}% required)"
        )


# ── Storage ────────────────────────────────────────────────────────────────


def _store_segments(
    db_path: str,
    track_id: int,
    segments: list[dict[str, Any]],
    *,
    model: str,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Replace any existing segments for the track with the new set.

    Returns the number of rows written.

    If *conn* is provided, reuses it instead of opening a new connection.
    This matters when the caller (e.g. the pipeline dispatcher) already holds
    an open write transaction on the same db file — a second connection
    trying to write there would block on the first until it times out.
    """
    owns_conn = conn is None
    if conn is None:
        conn = sqlite3.connect(db_path, timeout=60.0)
    try:
        def _write() -> None:
            conn.execute("DELETE FROM track_segments WHERE track_id = ?", (track_id,))
            for seg in segments:
                conn.execute(
                    """
                    INSERT INTO track_segments (
                        track_id, start_sec, end_sec, section_label, energy,
                        elements_present, mood, production_notes,
                        standout, standout_reason, visual_anchor, model_used
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        track_id,
                        seg["start_sec"],
                        seg["end_sec"],
                        seg["section_label"],
                        seg["energy"],
                        json.dumps(seg["elements_present"]),
                        seg["mood"],
                        seg["production_notes"],
                        seg["standout"],
                        seg["standout_reason"],
                        seg["visual_anchor"],
                        model,
                    ),
                )

        if owns_conn:
            with conn:
                _write()
        else:
            _write()  # caller owns the transaction and will commit it
        return len(segments)
    finally:
        if owns_conn:
            conn.close()


# ── Public entry point ─────────────────────────────────────────────────────


async def analyze_segments(
    file_path: str | Path,
    db_path: str,
    track_id: int,
    *,
    model: str = DEFAULT_OPENROUTER_MODEL,
    api_key: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Detect structural segments and describe them via Gemini.

    Three hard steps, no overlap:
      1. librosa computes boundaries deterministically from audio features.
      2. Gemini describes the content within each pre-computed window.
      3. Python validates and stores the result.

    Returns the validated segment list. Raises SegmentAnalysisError on failure.
    """
    path = Path(file_path)
    try:
        validate_audio_file(path)
    except (FileNotFoundError, GeminiClientError) as exc:
        raise SegmentAnalysisError(str(exc)) from exc

    key = _openrouter_key(api_key or os.environ.get("OPENROUTER_API_KEY"))

    logger.info(
        "Step 1/3 — detecting boundaries for track %d (%s)", track_id, path.name
    )
    total_duration, boundaries = _detect_boundaries(path)
    logger.info(
        "Detected %d boundaries covering %.1fs for track %d",
        len(boundaries),
        total_duration,
        track_id,
    )

    logger.info(
        "Step 2/3 — requesting Gemini descriptions for %d segments "
        "(in batches of %d)",
        len(boundaries),
        DESCRIPTION_BATCH_SIZE,
    )
    segments = await _call_gemini_for_descriptions(
        path, boundaries, model=model, api_key=key
    )

    logger.info("Step 3/3 — validating coverage")
    _validate_coverage(segments, total_duration)

    try:
        written = _store_segments(db_path, track_id, segments, model=model, conn=conn)
    except sqlite3.OperationalError as exc:
        # Surface as the documented error type so callers (dispatcher.py)
        # convert this to a visible pipeline_error feedback row and the
        # timeout scanner's automatic retry can pick it up. Left as a raw
        # OperationalError, this exception escapes uncaught and the track
        # is left stuck with no error trail and no recovery path.
        raise SegmentAnalysisError(f"Failed to store segments: {exc}") from exc
    logger.info("Wrote %d segments for track %d", written, track_id)
    return segments


def get_segments(db_path: str, track_id: int) -> list[dict[str, Any]]:
    """Read stored segments for a track."""
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, start_sec, end_sec, section_label, energy,
                   elements_present, mood, production_notes,
                   standout, standout_reason, visual_anchor,
                   model_used, analyzed_at
              FROM track_segments
             WHERE track_id = ?
             ORDER BY start_sec ASC
            """,
            (track_id,),
        ).fetchall()
    finally:
        conn.close()

    result = []
    for row in rows:
        item = dict(row)
        raw_elements = item.get("elements_present")
        if not raw_elements:
            raise SegmentAnalysisError(
                f"segment {item['id']} has no stored elements_present — "
                "either the writer skipped a validated field or the row is corrupt"
            )
        parsed = json.loads(raw_elements)
        if not isinstance(parsed, list):
            raise SegmentAnalysisError(
                f"segment {item['id']} elements_present is not a JSON array: {parsed!r}"
            )
        item["elements_present"] = parsed
        item["standout"] = bool(item["standout"])
        result.append(item)
    return result
