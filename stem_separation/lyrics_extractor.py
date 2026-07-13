"""Extract lyrics from a vocal stem using Gemini.

Sends the isolated vocal.wav to Gemini with a lyrics-focused prompt and
returns timestamped word-level transcription plus a clean lyrics block.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "vocal_analysis.txt"


class LyricsResult(BaseModel):
    """Output from vocal stem analysis."""

    lyrics_clean: str = Field(description="Full lyrics as plain text, verse/chorus labelled")
    lyrics_timestamped: list[dict] = Field(
        description="List of {line, start_time, end_time} dicts"
    )
    vocal_style: str = Field(description="Vocal delivery style (e.g. 'melodic rap, breathy falsetto')")
    vocal_observations: list[str] = Field(
        description="Notable moments: phrasing, breath control, tuning, emotion"
    )
    language: str = Field(default="english")
    explicit: bool = Field(default=False)


async def extract_lyrics(
    vocal_path: str | Path,
    *,
    api_key: str | None = None,
    model: str = "google/gemini-3.5-flash",
) -> LyricsResult:
    """Run Gemini (via OpenRouter) on a vocal stem WAV and return structured lyrics.

    Args:
        vocal_path: Path to the vocals.wav stem file.
        api_key:    OpenRouter API key (falls back to OPENROUTER_API_KEY env var).
        model:      OpenRouter model slug to use.

    Returns:
        LyricsResult with clean lyrics, timestamps, and vocal notes.

    Raises:
        FileNotFoundError: If the vocal file is missing.
        RuntimeError: If Gemini fails to parse the response.
    """
    from audio_analysis.gemini_client import openrouter_multimodal

    path = Path(vocal_path)
    if not path.exists():
        raise FileNotFoundError(f"Vocal stem not found: {path}")

    prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    raw = await openrouter_multimodal(
        prompt,
        model=model,
        data=path.read_bytes(),
        mime_type="audio/wav",
        api_key=api_key,
        temperature=0.1,
        max_tokens=4096,
    )

    try:
        data = json.loads(raw)
        return LyricsResult(**data)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to parse Gemini vocal response: {exc}\nRaw:\n{raw[:500]}"
        ) from exc
