"""Audio file format validation via magic bytes, size limits, and corruption checks."""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Size limits
MIN_FILE_SIZE = 10 * 1024       # 10 KB
MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB


class AudioFormat(StrEnum):
    """Supported audio formats."""

    WAV = "wav"
    FLAC = "flac"
    MP3 = "mp3"
    AIFF = "aiff"
    OGG = "ogg"


class ValidationResult(BaseModel):
    """Result of validating an audio file."""

    is_valid: bool
    file_path: str
    format: AudioFormat | None = None
    file_size: int = 0
    rejection_reason: str | None = None


# Magic byte signatures for audio format detection.
# Each entry is (offset, magic_bytes, format).
_MAGIC_SIGNATURES: list[tuple[int, bytes, AudioFormat]] = [
    # WAV: starts with RIFF....WAVE
    (0, b"RIFF", AudioFormat.WAV),
    # FLAC: starts with fLaC
    (0, b"fLaC", AudioFormat.FLAC),
    # OGG: starts with OggS
    (0, b"OggS", AudioFormat.OGG),
    # AIFF: starts with FORM....AIFF (or AIFC)
    (0, b"FORM", AudioFormat.AIFF),
    # MP3 with ID3 tag
    (0, b"ID3", AudioFormat.MP3),
]

# MP3 sync bytes (no ID3 tag) -- checked separately because the frame sync
# is only 11 bits (0xFF followed by 0xE0+ in the second byte).
_MP3_SYNC_BYTE_0 = 0xFF
_MP3_SYNC_MASK_1 = 0xE0  # top 3 bits of second byte must be set


def _detect_format(header: bytes) -> AudioFormat | None:
    """Detect audio format from the first bytes of a file.

    Args:
        header: At least the first 12 bytes of the file.

    Returns:
        Detected AudioFormat, or None if unrecognised.
    """
    if len(header) < 4:
        return None

    # Check fixed-offset signatures first.
    for offset, magic, fmt in _MAGIC_SIGNATURES:
        end = offset + len(magic)
        if len(header) >= end and header[offset:end] == magic:
            # WAV: additionally verify the WAVE chunk at offset 8
            if fmt is AudioFormat.WAV:
                if len(header) >= 12 and header[8:12] == b"WAVE":
                    return AudioFormat.WAV
                # RIFF but not WAVE -- not a WAV file
                continue
            # AIFF: verify AIFF or AIFC at offset 8
            if fmt is AudioFormat.AIFF:
                if len(header) >= 12 and header[8:12] in (b"AIFF", b"AIFC"):
                    return AudioFormat.AIFF
                continue
            return fmt

    # MP3 frame sync (no ID3 tag).
    if len(header) >= 2:
        if header[0] == _MP3_SYNC_BYTE_0 and (header[1] & _MP3_SYNC_MASK_1) == _MP3_SYNC_MASK_1:
            return AudioFormat.MP3

    return None


def _check_wav_headers(path: Path) -> str | None:
    """Basic corruption check for WAV files.

    Returns a rejection reason string if corrupt, else None.
    """
    try:
        with path.open("rb") as f:
            header = f.read(44)  # standard WAV header is 44 bytes
        if len(header) < 44:
            return "WAV header too short"
        # Verify sub-chunk "fmt " exists somewhere in the header.
        if b"fmt " not in header:
            return "WAV missing fmt sub-chunk"
    except OSError as exc:
        return f"Cannot read WAV header: {exc}"
    return None


def _check_flac_headers(path: Path) -> str | None:
    """Basic corruption check for FLAC files."""
    try:
        with path.open("rb") as f:
            header = f.read(8)
        if len(header) < 8:
            return "FLAC header too short"
        # Byte 4 should be a metadata block header; bit 1-6 encode block type.
        block_type = header[4] & 0x7F
        if block_type > 6:
            return f"FLAC unexpected metadata block type {block_type}"
    except OSError as exc:
        return f"Cannot read FLAC header: {exc}"
    return None


def _check_corruption(path: Path, fmt: AudioFormat) -> str | None:
    """Run format-specific corruption checks.

    Returns a rejection reason string if corrupt, else None.
    """
    from collections.abc import Callable  # local import avoids circular dependency
    checks: dict[AudioFormat, Callable[[Path], str | None]] = {
        AudioFormat.WAV: _check_wav_headers,
        AudioFormat.FLAC: _check_flac_headers,
    }
    checker = checks.get(fmt)
    if checker is not None:
        return checker(path)
    # For formats without a dedicated checker, verify the file is readable.
    try:
        with path.open("rb") as f:
            f.read(1024)
    except OSError as exc:
        return f"Cannot read file: {exc}"
    return None


def validate_audio_file(file_path: str | Path) -> ValidationResult:
    """Validate an audio file by magic bytes, size, and basic corruption check.

    Args:
        file_path: Path to the file to validate.

    Returns:
        A ``ValidationResult`` describing the outcome.
    """
    path = Path(file_path)
    str_path = str(path)

    # --- Existence ---
    if not path.is_file():
        return ValidationResult(
            is_valid=False,
            file_path=str_path,
            rejection_reason="File does not exist or is not a regular file",
        )

    # --- Size limits ---
    try:
        file_size = path.stat().st_size
    except OSError as exc:
        return ValidationResult(
            is_valid=False,
            file_path=str_path,
            rejection_reason=f"Cannot stat file: {exc}",
        )

    if file_size < MIN_FILE_SIZE:
        return ValidationResult(
            is_valid=False,
            file_path=str_path,
            file_size=file_size,
            rejection_reason=f"File too small ({file_size} bytes, minimum {MIN_FILE_SIZE})",
        )

    if file_size > MAX_FILE_SIZE:
        return ValidationResult(
            is_valid=False,
            file_path=str_path,
            file_size=file_size,
            rejection_reason=f"File too large ({file_size} bytes, maximum {MAX_FILE_SIZE})",
        )

    # --- Magic bytes ---
    try:
        with path.open("rb") as f:
            header = f.read(12)
    except OSError as exc:
        return ValidationResult(
            is_valid=False,
            file_path=str_path,
            file_size=file_size,
            rejection_reason=f"Cannot read file header: {exc}",
        )

    fmt = _detect_format(header)
    if fmt is None:
        return ValidationResult(
            is_valid=False,
            file_path=str_path,
            file_size=file_size,
            rejection_reason="Unrecognised audio format (magic bytes do not match any supported format)",
        )

    # --- Corruption check ---
    corruption = _check_corruption(path, fmt)
    if corruption is not None:
        return ValidationResult(
            is_valid=False,
            file_path=str_path,
            file_size=file_size,
            format=fmt,
            rejection_reason=f"File appears corrupt: {corruption}",
        )

    logger.info("Validated %s as %s (%d bytes)", path.name, fmt.value, file_size)
    return ValidationResult(
        is_valid=True,
        file_path=str_path,
        file_size=file_size,
        format=fmt,
    )
