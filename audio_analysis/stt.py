"""Speech-to-text transcription via ElevenLabs Scribe.

Used by Live Mode to transcribe the artist's spoken replies in the round
table. Unlike tts.py, nothing is cached — every utterance is unique.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"

# Scribe v2: ElevenLabs' current-gen ASR model (successor to scribe_v1).
# https://elevenlabs.io/docs/api-reference/speech-to-text/convert
STT_MODEL_ID = "scribe_v2"

# 429 (rate limit) and 5xx are transient — retry with backoff.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3


class SttError(Exception):
    """Raised when transcription fails for a recoverable reason."""


def _load_repo_env_value(key: str) -> str | None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return None
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return None


def _elevenlabs_key() -> str:
    key = (_load_repo_env_value("ELEVENLABS_API_KEY") or os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    if not key:
        raise SttError("ELEVENLABS_API_KEY is not set")
    return key


def transcribe(audio_bytes: bytes, filename: str, content_type: str) -> str:
    """Transcribe raw audio bytes and return the recognized text."""
    if not audio_bytes:
        raise SttError("No audio data provided")

    headers = {"xi-api-key": _elevenlabs_key(), "Accept": "application/json"}
    files = {"file": (filename, audio_bytes, content_type or "application/octet-stream")}
    data = {"model_id": STT_MODEL_ID}

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = httpx.post(
                ELEVENLABS_STT_URL, headers=headers, files=files, data=data, timeout=60.0
            )
            response.raise_for_status()
            payload = response.json()
            text = payload.get("text")
            if not isinstance(text, str):
                raise SttError(f"ElevenLabs STT response missing 'text' field: {payload!r}")
            return text
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in _RETRYABLE_STATUS_CODES or attempt == _MAX_RETRIES - 1:
                raise SttError(
                    f"ElevenLabs STT returned {exc.response.status_code}: {exc.response.text[:200]}"
                ) from exc
            last_exc = exc
            time.sleep(2**attempt)  # 1s, 2s
        except httpx.HTTPError as exc:
            if attempt == _MAX_RETRIES - 1:
                raise SttError(f"ElevenLabs STT request failed: {exc}") from exc
            last_exc = exc
            time.sleep(2**attempt)

    raise SttError(f"ElevenLabs STT request failed after {_MAX_RETRIES} attempts: {last_exc}")
