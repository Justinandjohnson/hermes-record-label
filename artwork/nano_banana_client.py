"""OpenRouter client for Google's Nano Banana image generation.

NanoBanana 2 (Gemini 3.1 Flash Image, slug `google/gemini-3.1-flash-image`)
and NanoBanana Pro (Gemini 3 Pro Image, slug `google/gemini-3-pro-image-preview`)
both speak the OpenRouter chat-completions protocol with `modalities` set to
`["image", "text"]`. The response carries images as base64 data URLs inside
`choices[0].message.images`.

This module exposes one async function — `generate(prompt, ...)` — that
returns a saved local file path. The caller is responsible for writing the
`artwork_generations` row.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from audio_analysis.gemini_client import _openrouter_key

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

NANO_BANANA_PRO = "google/gemini-3-pro-image-preview"
NANO_BANANA_2 = "google/gemini-3.1-flash-image"

DEFAULT_MODEL = NANO_BANANA_PRO


class NanoBananaError(Exception):
    """Raised when image generation fails for a recoverable reason."""


@dataclass
class GeneratedImage:
    """One generated variant — the saved file path + the model that made it."""

    file_path: Path
    model: str
    mime_type: str


def _slug(text: str, *, max_length: int = 40) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return cleaned[:max_length] or "untitled"


def _decode_data_url(data_url: str) -> tuple[bytes, str]:
    """Decode a data:image/...;base64,... URL → (bytes, mime_type)."""
    if not data_url.startswith("data:"):
        raise NanoBananaError(f"Expected data URL, got: {data_url[:60]}…")
    header, _, payload = data_url.partition(",")
    if not payload:
        raise NanoBananaError("Data URL has no payload")
    mime_match = re.match(r"data:([^;]+);base64", header)
    if not mime_match:
        raise NanoBananaError(f"Cannot parse data URL header: {header}")
    mime_type = mime_match.group(1)
    try:
        return base64.b64decode(payload), mime_type
    except (ValueError, binascii.Error) as exc:
        raise NanoBananaError(f"Base64 decode failed: {exc}") from exc


def _mime_to_ext(mime_type: str) -> str:
    return {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
    }.get(mime_type, "png")


# ── Public API ─────────────────────────────────────────────────────────────


async def generate(
    prompt: str,
    *,
    output_dir: Path,
    label: str,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    timeout: float = 180.0,
) -> GeneratedImage:
    """Send a prompt to NanoBanana via OpenRouter, save the returned image.

    Args:
        prompt: The full NanoBanana prompt (Maren writes these).
        output_dir: Directory to save the returned image into. Created if missing.
        label: Used to construct the filename. Should be unique per variant
            (e.g. "track-42-v1-medium").
        model: NanoBanana model slug. Default is Pro.
        api_key: Optional override. Falls back to OPENROUTER_API_KEY.

    Returns:
        GeneratedImage with the local file path.

    Raises:
        NanoBananaError on any failure.
    """
    key = _openrouter_key(api_key or os.environ.get("OPENROUTER_API_KEY"))
    if not key:
        raise NanoBananaError("OPENROUTER_API_KEY is not set")

    output_dir.mkdir(parents=True, exist_ok=True)

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://ai-record-label.local",
        "X-Title": "AI Record Label",
    }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "modalities": ["image", "text"],
    }

    logger.info("Sending NanoBanana prompt (%s, %d chars)", model, len(prompt))
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(OPENROUTER_URL, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        raise NanoBananaError(
            f"OpenRouter HTTP {exc.response.status_code}: {exc.response.text[:400]}"
        ) from exc
    except httpx.HTTPError as exc:
        raise NanoBananaError(f"OpenRouter request failed: {exc}") from exc

    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise NanoBananaError(f"Unexpected OpenRouter response shape: {data}") from exc

    images = message.get("images") or []
    if not images:
        raise NanoBananaError(
            f"No images returned. Message content was: {message.get('content', '')[:400]}"
        )

    first = images[0]
    image_field = first.get("image_url")
    if isinstance(image_field, dict):
        data_url = image_field.get("url", "")
    else:
        data_url = image_field or ""
    if not data_url:
        raise NanoBananaError(f"Image entry missing data URL: {first}")

    image_bytes, mime_type = _decode_data_url(data_url)
    ext = _mime_to_ext(mime_type)
    file_path = output_dir / f"{_slug(label)}-{int(time.time() * 1000)}.{ext}"
    file_path.write_bytes(image_bytes)
    logger.info("Saved NanoBanana image to %s (%d bytes)", file_path, len(image_bytes))

    return GeneratedImage(file_path=file_path, model=model, mime_type=mime_type)
