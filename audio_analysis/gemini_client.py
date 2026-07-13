"""OpenRouter client for Gemini multimodal audio analysis."""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

import httpx
import librosa

from .models import AudioAnalysis, MixObservation

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS: dict[str, str] = {
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".mp3": "audio/mpeg",
    ".aiff": "audio/aiff",
    ".aif": "audio/aiff",
    ".ogg": "audio/ogg",
}

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

_PROMPT_PATH = Path(__file__).parent / "prompts" / "analysis_prompt.txt"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "google/gemini-3.1-pro-preview"
_SECTION_TERMS = (
    "intro",
    "verse",
    "chorus",
    "pre-chorus",
    "bridge",
    "drop",
    "breakdown",
    "outro",
    "switch",
    "transition",
)


class GeminiClientError(Exception):
    """Base error for Gemini client operations."""


class UnsupportedFormatError(GeminiClientError):
    """Raised when the audio file format is not supported."""


class FileTooLargeError(GeminiClientError):
    """Raised when the audio file exceeds the size limit."""


class AnalysisParseError(GeminiClientError):
    """Raised when Gemini's response cannot be parsed into AudioAnalysis."""


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _format_mmss(seconds: float) -> str:
    total_seconds = max(0, round(seconds))
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes}:{secs:02d}"


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _local_audio_grounding_note(file_path: Path) -> tuple[str, bool]:
    y, sr = librosa.load(str(file_path), sr=22050, mono=True)
    duration_seconds = librosa.get_duration(y=y, sr=sr)
    if duration_seconds <= 0:
        return (
            "Local audio feature pass: duration unavailable. "
            "Do not invent section labels or transitions unless they are clearly audible.",
            False,
        )

    hop_length = 512
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)

    window_seconds = 8.0
    frames_per_window = max(1, int(window_seconds * sr / hop_length))
    window_vectors: list[list[float]] = []
    rms_means: list[float] = []
    onset_means: list[float] = []

    frame_count = min(len(rms), len(onset_env), chroma.shape[1])
    for start in range(0, frame_count, frames_per_window):
        end = min(frame_count, start + frames_per_window)
        if end - start < max(4, frames_per_window // 3):
            continue
        window_rms = rms[start:end]
        window_onset = onset_env[start:end]
        window_chroma = chroma[:, start:end]
        rms_mean = float(window_rms.mean())
        onset_mean = float(window_onset.mean())
        chroma_means = [float(value) for value in window_chroma.mean(axis=1)]
        rms_means.append(rms_mean)
        onset_means.append(onset_mean)
        window_vectors.append([rms_mean, onset_mean, *chroma_means])

    adjacent_similarities = [
        _cosine_similarity(window_vectors[idx], window_vectors[idx + 1])
        for idx in range(len(window_vectors) - 1)
    ]
    median_adjacent_similarity = (
        sorted(adjacent_similarities)[len(adjacent_similarities) // 2]
        if adjacent_similarities
        else 0.0
    )
    rms_spread = (max(rms_means) - min(rms_means)) if rms_means else 0.0
    onset_spread = (max(onset_means) - min(onset_means)) if onset_means else 0.0
    loop_like = (
        len(window_vectors) >= 4
        and median_adjacent_similarity >= 0.985
        and rms_spread <= 0.035
        and onset_spread <= 0.12
    )

    classification = (
        "likely loop-based / minimal structural variation"
        if loop_like
        else "meaningful structural change may be present"
    )
    note = (
        "Local audio feature pass (treat these as grounding facts): "
        f"duration={_format_mmss(duration_seconds)}, "
        f"window_count={len(window_vectors)}, "
        f"adjacent_window_similarity={median_adjacent_similarity:.3f}, "
        f"rms_window_spread={rms_spread:.3f}, "
        f"onset_window_spread={onset_spread:.3f}, "
        f"classification={classification}. "
        "If classification says loop-based/minimal structural variation, "
        "do not invent verse/chorus/drop transitions."
    )
    return note, loop_like


def _sanitize_loop_like_analysis(analysis: AudioAnalysis) -> AudioAnalysis:
    filtered_moments = [
        moment
        for moment in analysis.notable_moments
        if not any(term in moment.description.lower() for term in _SECTION_TERMS)
    ]
    analysis.structure = {}
    analysis.notable_moments = filtered_moments
    guard_text = (
        "Local feature pass indicates a loop-based arrangement with minimal structural "
        "variation across the full track. Treat section changes cautiously."
    )
    if not any(guard_text == observation.observation for observation in analysis.mix_observations):
        analysis.mix_observations.insert(
            0,
            MixObservation(timestamp="0:00", observation=guard_text),
        )
    return analysis


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


def _openrouter_key(api_key: str | None) -> str:
    key = (
        api_key
        or _load_repo_env_value("OPENROUTER_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
        or ""
    ).strip()
    if not key:
        raise GeminiClientError("OPENROUTER_API_KEY is not set")
    return key


def validate_audio_file(file_path: Path) -> str:
    """Validate the audio file and return its MIME type.

    Raises:
        FileNotFoundError: If the file does not exist.
        UnsupportedFormatError: If the format is not supported.
        FileTooLargeError: If the file exceeds 50 MB.
        GeminiClientError: If the file is empty or unreadable.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    suffix = file_path.suffix.lower()
    if suffix not in SUPPORTED_FORMATS:
        raise UnsupportedFormatError(
            f"Unsupported format '{suffix}'. Supported: {', '.join(SUPPORTED_FORMATS)}"
        )

    size = file_path.stat().st_size
    if size == 0:
        raise GeminiClientError(f"Audio file is empty: {file_path}")
    if size > MAX_FILE_SIZE_BYTES:
        raise FileTooLargeError(
            f"File is {size / (1024 * 1024):.1f} MB, max is "
            f"{MAX_FILE_SIZE_BYTES / (1024 * 1024):.0f} MB"
        )

    return SUPPORTED_FORMATS[suffix]


def _parse_response(raw_text: str) -> dict:
    """Parse Gemini's JSON response, stripping markdown fences if present."""
    text = raw_text.strip()
    if text.startswith("```"):
        # Strip ```json ... ``` wrapper
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)


async def analyze_audio(
    file_path: str | Path,
    *,
    model: str = DEFAULT_OPENROUTER_MODEL,
    api_key: str | None = None,
) -> AudioAnalysis:
    """Analyze an audio file with Gemini 3.1 Pro through OpenRouter.

    Args:
        file_path: Path to the audio file.
        model: OpenRouter model slug.
        api_key: Optional OpenRouter API key; if None, uses OPENROUTER_API_KEY.

    Returns:
        AudioAnalysis with all fields populated from Gemini's response.

    Raises:
        GeminiClientError: On validation, API, or parsing errors.
    """
    path = Path(file_path)
    mime_type = validate_audio_file(path)
    audio_format = path.suffix.lower().lstrip(".")

    prompt_text = _load_prompt()
    key = _openrouter_key(api_key)
    grounding_note, loop_like = _local_audio_grounding_note(path)

    logger.info("Sending %s (%s) to OpenRouter model %s", path.name, mime_type, model)

    try:
        audio_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "text", "text": grounding_note},
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": audio_b64,
                                "format": audio_format,
                            },
                        },
                    ],
                }
            ],
            "temperature": 0.3,
            "max_tokens": 8192,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://ai-record-label.local",
            "X-Title": "AI Record Label",
        }
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(OPENROUTER_URL, headers=headers, json=payload)
            response.raise_for_status()
        body = response.json()
    except Exception as exc:
        detail = str(exc)
        if isinstance(exc, httpx.HTTPStatusError):
            detail = exc.response.text[:1000]
        raise GeminiClientError(f"OpenRouter API error: {detail}") from exc

    try:
        raw_text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiClientError(f"OpenRouter response missing message content: {body}") from exc
    if not raw_text:
        raise GeminiClientError("OpenRouter returned an empty response")

    logger.debug("Raw OpenRouter response length: %d chars", len(raw_text))

    try:
        data = _parse_response(raw_text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AnalysisParseError(
            f"Failed to parse Gemini response as JSON: {exc}"
        ) from exc

    try:
        # model_validate lets Pydantic coerce dicts into typed sub-models
        # (EnergyCurvePoint, MixObservation, NotableMoment) automatically.
        analysis = AudioAnalysis.model_validate(
            {
                "model_used": model,
                "bpm": data.get("bpm"),
                "musical_key": data.get("musical_key"),
                "energy_curve": data.get("energy_curve", []),
                "structure": data.get("structure", {}),
                "instruments": data.get("instruments", []),
                "genre_tags": data.get("genre_tags", []),
                "mood_tags": data.get("mood_tags", []),
                "mix_observations": data.get("mix_observations", []),
                "notable_moments": data.get("notable_moments", []),
                "raw_response": raw_text,
            }
        )
    except Exception as exc:
        raise AnalysisParseError(
            f"Failed to build AudioAnalysis from Gemini data: {exc}"
        ) from exc

    if loop_like:
        analysis = _sanitize_loop_like_analysis(analysis)

    return analysis
