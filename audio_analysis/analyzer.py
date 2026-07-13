"""Main entry point for audio analysis: analyze(file_path, db_path) -> AudioAnalysis."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .gemini_client import (
    DEFAULT_OPENROUTER_MODEL,
    GeminiClientError,
    analyze_audio,
    validate_audio_file,
)
from .memory_builder import build_memory, store_analysis_sync
from .models import AudioAnalysis

logger = logging.getLogger(__name__)


class AnalyzerError(Exception):
    """Top-level error for the analyzer module."""


async def analyze_async(
    file_path: str,
    db_path: str,
    *,
    track_id: int = 0,
    model: str = DEFAULT_OPENROUTER_MODEL,
    api_key: str | None = None,
    skip_memory: bool = False,
) -> AudioAnalysis:
    """Analyze an audio file and store results.

    Args:
        file_path: Path to the audio file on disk.
        db_path: Path to the SQLite database.
        track_id: The track's ID in the tracks table (0 if unknown).
        model: Gemini model to use.
        api_key: Optional Gemini API key.
        skip_memory: If True, skip the memory-building step.

    Returns:
        A fully populated AudioAnalysis.

    Raises:
        AnalyzerError: On any failure (wraps underlying errors).
    """
    path = Path(file_path)

    # --- Validate ---
    try:
        validate_audio_file(path)
    except (FileNotFoundError, GeminiClientError) as exc:
        raise AnalyzerError(str(exc)) from exc

    # --- Analyze via Gemini ---
    try:
        analysis = await analyze_audio(path, model=model, api_key=api_key)
    except GeminiClientError as exc:
        raise AnalyzerError(f"Audio analysis failed: {exc}") from exc

    analysis.track_id = track_id

    # --- Store and build memory ---
    if skip_memory:
        store_analysis_sync(db_path, track_id, analysis)
        logger.info("Analysis stored (memory building skipped) for track %d", track_id)
    else:
        try:
            track_context = await build_memory(
                db_path, track_id, analysis, model=model, api_key=api_key,
            )
            logger.info(
                "Analysis + memory stored for track %d (%d confirmed patterns)",
                track_id,
                len(track_context.confirmed_patterns),
            )
        except Exception:
            # Memory building is non-critical; store the analysis anyway
            logger.exception("Memory building failed; storing analysis only")
            store_analysis_sync(db_path, track_id, analysis)

    return analysis


def analyze(
    file_path: str,
    db_path: str,
    *,
    track_id: int = 0,
    model: str = DEFAULT_OPENROUTER_MODEL,
    api_key: str | None = None,
    skip_memory: bool = False,
) -> AudioAnalysis:
    """Synchronous wrapper around analyze_async.

    Creates or reuses an event loop to run the async pipeline.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # We're inside an existing event loop (e.g. Jupyter, Hermes)
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                asyncio.run,
                analyze_async(
                    file_path,
                    db_path,
                    track_id=track_id,
                    model=model,
                    api_key=api_key,
                    skip_memory=skip_memory,
                ),
            )
            return future.result()
    else:
        return asyncio.run(
            analyze_async(
                file_path,
                db_path,
                track_id=track_id,
                model=model,
                api_key=api_key,
                skip_memory=skip_memory,
            )
        )
