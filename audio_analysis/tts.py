"""Agent voice playback via ElevenLabs or Fish Audio (cloud / self-hosted).

Provider is chosen by the `voice_provider` key in data_dir/settings.json:
  - "elevenlabs" (default): ElevenLabs Flash v2.5, one fixed voice per agent.
  - "fish-cloud": Fish Audio hosted API (https://api.fish.audio/v1/tts).
  - "fish-local": self-hosted fish-speech tools.api_server (default
    http://127.0.0.1:8090), prewarmed at app startup when a good GPU exists.

Audio for a given feedback message is generated once and cached to disk,
keyed by message id + provider (message text for a given id never changes).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
FISH_CLOUD_TTS_URL = "https://api.fish.audio/v1/tts"
FISH_LOCAL_BASE_URL = os.environ.get("FISH_LOCAL_URL", "http://127.0.0.1:8090").rstrip("/")

# Flash v2.5: same ~75ms latency class as Turbo, 50% cheaper per character,
# ElevenLabs' own recommendation over Turbo for bulk/real-time use.
# https://elevenlabs.io/docs/overview/models
TTS_MODEL_ID = "eleven_flash_v2_5"

# Cloud model selection goes in the HEADER (body "model" is ignored).
# Default: the free s2.1-pro tier (no API credit needed).
FISH_CLOUD_MODEL = os.environ.get("FISH_MODEL", "s2.1-pro-free").strip()

VALID_PROVIDERS = {"elevenlabs", "fish-cloud", "fish-local"}

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

# Fish Audio reference voice ids (cloud) or saved reference names (local),
# per agent. Cloud ids come from your Fish account's voice library; local
# names map to reference clips under the fish-speech references directory.
AGENT_FISH_VOICE_IDS: dict[str, str] = json.loads(
    os.environ.get("FISH_AGENT_VOICE_IDS", "{}")
)


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


def _env_key(key: str) -> str:
    value = (_load_repo_env_value(key) or os.environ.get(key) or "").strip()
    return value


def _current_provider(data_dir: Path) -> str:
    settings_path = data_dir / "settings.json"
    provider = "elevenlabs"
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
            provider = str(data.get("voice_provider") or provider).strip()
        except (json.JSONDecodeError, OSError):
            pass
    return provider if provider in VALID_PROVIDERS else "elevenlabs"


def _fish_reference_id(agent: str, data_dir: Path | None = None) -> str | None:
    """Per-agent Fish voice: settings map (UI-editable) wins, then env, then None."""
    if data_dir is not None:
        settings_path = data_dir / "settings.json"
        if settings_path.exists():
            try:
                mapping = json.loads(settings_path.read_text()).get("fish_voice_map") or {}
                voice_id = mapping.get(agent)
                if voice_id:
                    return str(voice_id)
            except (json.JSONDecodeError, OSError):
                pass
    return AGENT_FISH_VOICE_IDS.get(agent)


def synthesize(data_dir: Path, message_id: int, agent: str, text: str) -> Path:
    """Return the local audio path for this message, generating it if missing."""
    provider = _current_provider(data_dir)
    ext = "wav" if provider == "fish-local" else "mp3"

    cache_dir = data_dir / "tts"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"msg-{message_id}-{provider}.{ext}"
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return cache_path

    if provider == "fish-cloud":
        content, media_type = _synthesize_fish_cloud(agent, text, data_dir), "audio/mpeg"
    elif provider == "fish-local":
        content, media_type = _synthesize_fish_local(agent, text), "audio/wav"
    else:
        content, media_type = _synthesize_elevenlabs(agent, text), "audio/mpeg"

    cache_path.write_bytes(content)
    cache_path.with_name(cache_path.name + ".type").write_text(media_type)
    return cache_path


def cached_media_type(cache_path: Path) -> str:
    type_file = Path(str(cache_path) + ".type")
    if type_file.exists():
        stored = type_file.read_text().strip()
        if stored:
            return stored
    return "audio/mpeg" if cache_path.suffix == ".mp3" else "audio/wav"


def _synthesize_elevenlabs(agent: str, text: str) -> bytes:
    voice_id = AGENT_VOICE_IDS.get(agent)
    if voice_id is None:
        raise TtsError(f"No voice configured for agent {agent!r}")
    api_key = _env_key("ELEVENLABS_API_KEY")
    if not api_key:
        raise TtsError("ELEVENLABS_API_KEY is not set")

    url = ELEVENLABS_TTS_URL.format(voice_id=voice_id)
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {"text": text, "model_id": TTS_MODEL_ID}

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=30.0)
            response.raise_for_status()
            return response.content
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


def _synthesize_fish_cloud(agent: str, text: str, data_dir: Path | None = None) -> bytes:
    api_key = _env_key("FISH_API_KEY")
    if not api_key:
        raise TtsError("FISH_API_KEY is not set (needed for fish-cloud voices)")

    payload: dict[str, object] = {"text": text, "format": "mp3"}
    reference_id = _fish_reference_id(agent, data_dir)
    if reference_id:
        payload["reference_id"] = reference_id
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if FISH_CLOUD_MODEL:
        headers["model"] = FISH_CLOUD_MODEL

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = httpx.post(
                FISH_CLOUD_TTS_URL, headers=headers, json=payload, timeout=60.0
            )
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in _RETRYABLE_STATUS_CODES or attempt == _MAX_RETRIES - 1:
                raise TtsError(
                    f"Fish cloud returned {exc.response.status_code}: {exc.response.text[:200]}"
                ) from exc
            last_exc = exc
            time.sleep(2**attempt)
        except httpx.HTTPError as exc:
            if attempt == _MAX_RETRIES - 1:
                raise TtsError(f"Fish cloud request failed: {exc}") from exc
            last_exc = exc
            time.sleep(2**attempt)

    raise TtsError(f"Fish cloud request failed after {_MAX_RETRIES} attempts: {last_exc}")


def _synthesize_fish_local(agent: str, text: str) -> bytes:
    payload: dict[str, object] = {"text": text}
    # Local references live at references/<name>/; exported folders use hyphens
    # (e.g. a-and-r), so default to that form when no explicit mapping is set.
    reference_id = _fish_reference_id(agent) or agent.replace("_", "-")
    payload["reference_id"] = reference_id
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = httpx.post(
                f"{FISH_LOCAL_BASE_URL}/v1/tts",
                json=payload,
                timeout=120.0,
            )
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in _RETRYABLE_STATUS_CODES or attempt == _MAX_RETRIES - 1:
                raise TtsError(
                    f"Fish local returned {exc.response.status_code}: {exc.response.text[:200]}"
                ) from exc
            last_exc = exc
            time.sleep(2**attempt)
        except httpx.HTTPError as exc:
            if attempt == _MAX_RETRIES - 1:
                raise TtsError(
                    f"Fish local server at {FISH_LOCAL_BASE_URL} is unreachable: {exc}"
                ) from exc
            last_exc = exc
            time.sleep(2**attempt)

    raise TtsError(f"Fish local request failed after {_MAX_RETRIES} attempts: {last_exc}")


def local_server_ready(timeout: float = 1.0) -> bool:
    try:
        response = httpx.get(f"{FISH_LOCAL_BASE_URL}/v1/health", timeout=timeout)
        return response.is_success
    except httpx.HTTPError:
        return False
