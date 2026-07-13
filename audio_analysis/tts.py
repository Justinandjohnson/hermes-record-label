"""Agent voice playback via ElevenLabs.

Each agent persona gets a fixed ElevenLabs voice. Audio for a given feedback
message is generated once and cached to disk, keyed by message id (message
text for a given id never changes).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

# Flash v2.5: same ~75ms latency class as Turbo, 50% cheaper per character,
# ElevenLabs' own recommendation over Turbo for bulk/real-time use.
# https://elevenlabs.io/docs/overview/models
TTS_MODEL_ID = "eleven_flash_v2_5"

# 429 (rate limit) and 5xx are transient — retry with backoff.
# 400/401/403/422 are not retryable. https://elevenlabs.io/docs/eleven-api/resources/errors
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3

# Voice chosen per agent persona from the account's real voice list
# (fetched via GET /v1/voices — never guessed).
AGENT_VOICE_IDS: dict[str, str] = {
    "manager": "onwK4e9ZLuTAKqWW03F9",  # Daniel — Steady Broadcaster (Conductor)
    "a_and_r": "IKne3meq5aSn9XLyUdCD",  # Charlie — Deep, Confident, Energetic (A&R)
    "kallman": "N2lVS1w4EtoT3dr4eOWO",  # Callum — Husky Trickster (First instinct)
    "janick": "JBFqnCBsd6RMkjVDRZzb",  # George — Warm, Captivating Storyteller (Vision)
    "rhone": "bIHbv24MWmeRgasZH58o",  # Will — Relaxed Optimist (Culture)
    "rubin": "nPczCjzI2devNBz1zQrb",  # Brian — Deep, Resonant, Comforting (Essence)
    "creative_director": "cgSgspJ2msm6clMCkdW9",  # Jessica — Playful, Bright, Warm (Creative)
    "bandcamp": "XrExE9yKIg1WjnnlVkGX",  # Matilda — Knowledgeable, Professional (Release)
    "intake": "SAz9YHcvj6GT2YYXdXww",  # River — Relaxed, Neutral, Informative (Ingest)
    "system": "cjVigY5qzO86Huf0OWal",  # Eric — Smooth, Trustworthy (Automation)
}


class TtsError(Exception):
    """Raised when speech synthesis fails for a recoverable reason."""


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
        raise TtsError("ELEVENLABS_API_KEY is not set")
    return key


def synthesize(data_dir: Path, message_id: int, agent: str, text: str) -> Path:
    """Return the local mp3 path for this message, generating it if missing."""
    voice_id = AGENT_VOICE_IDS.get(agent)
    if voice_id is None:
        raise TtsError(f"No voice configured for agent {agent!r}")

    cache_dir = data_dir / "tts"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"msg-{message_id}.mp3"
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return cache_path

    url = ELEVENLABS_TTS_URL.format(voice_id=voice_id)
    headers = {
        "xi-api-key": _elevenlabs_key(),
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {"text": text, "model_id": TTS_MODEL_ID}

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=30.0)
            response.raise_for_status()
            cache_path.write_bytes(response.content)
            return cache_path
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in _RETRYABLE_STATUS_CODES or attempt == _MAX_RETRIES - 1:
                raise TtsError(
                    f"ElevenLabs returned {exc.response.status_code}: {exc.response.text[:200]}"
                ) from exc
            last_exc = exc
            time.sleep(2**attempt)  # 1s, 2s
        except httpx.HTTPError as exc:
            if attempt == _MAX_RETRIES - 1:
                raise TtsError(f"ElevenLabs request failed: {exc}") from exc
            last_exc = exc
            time.sleep(2**attempt)

    raise TtsError(f"ElevenLabs request failed after {_MAX_RETRIES} attempts: {last_exc}")
