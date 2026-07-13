"""Best-effort metadata extraction from audio filenames.

Handles common DAW export naming conventions, version markers, and
production stage tags.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

# --- Compiled patterns -----------------------------------------------------------

# Version markers: "v2", "V3", "version 4", "rev5", "revision 6"
_VERSION_RE = re.compile(
    r"""
    (?:^|[\s_\-.(])           # preceded by separator or start
    (?:v|version|rev|revision) # keyword
    [\s._\-]?                  # optional separator
    (\d+)                      # version number
    (?:[\s_\-.)$]|$)           # followed by separator or end
    """,
    re.IGNORECASE | re.VERBOSE,
)

# BPM hints: "120bpm", "120 bpm", "bpm120", "bpm 120"
_BPM_RE = re.compile(
    r"""
    (?:^|[\s_\-.(])
    (?:
        (\d{2,3})\s*bpm        # number then bpm
        |
        bpm\s*(\d{2,3})        # bpm then number
    )
    (?:[\s_\-.)$]|$)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Tags to detect in filenames (lowercased for matching).
_ROUGH_TAGS = frozenset({"rough", "demo", "scratch", "sketch", "wip", "draft"})
_FINAL_TAGS = frozenset({"final", "master", "mastered", "release"})
_MIX_TAGS = frozenset({"mix", "mixdown", "bounce", "stem"})

# Characters we strip from the cleaned title.
_CLEANUP_RE = re.compile(r"[\s_\-]+")
_PAREN_RE = re.compile(r"\([^)]*\)")
_BRACKET_RE = re.compile(r"\[[^\]]*\]")
# Trailing Unix timestamps / export counters: 8+ digit number at end of stem.
_TRAILING_DIGITS_RE = re.compile(r"[\s_\-]+\d{8,}\s*$")


class ParsedFilename(BaseModel):
    """Metadata extracted from an audio filename."""

    title: str = Field(description="Best-guess track title")
    version_hint: int | None = Field(default=None, description="Version number from filename")
    bpm_hint: int | None = Field(default=None, description="BPM extracted from filename")
    tags: list[str] = Field(default_factory=list, description="Detected tags: rough, final, mix, etc.")
    is_rough: bool = Field(default=False, description="Appears to be a rough/demo version")
    is_final: bool = Field(default=False, description="Appears to be a final/master version")
    raw_filename: str = Field(description="Original filename before parsing")


def _extract_tags(text: str) -> list[str]:
    """Extract production-stage tags from text."""
    lower = text.lower()
    # Tokenise on common separators.
    tokens = set(re.split(r"[\s_\-.()\[\]]+", lower))
    found: list[str] = []
    for tag in sorted(_ROUGH_TAGS | _FINAL_TAGS | _MIX_TAGS):
        if tag in tokens:
            found.append(tag)
    return found


def _clean_title(stem: str) -> str:
    """Remove version markers, BPM hints, tags, and parentheticals to isolate the title."""
    cleaned = stem

    # Remove parenthetical and bracketed groups.
    cleaned = _PAREN_RE.sub(" ", cleaned)
    cleaned = _BRACKET_RE.sub(" ", cleaned)

    # Remove version markers.
    cleaned = _VERSION_RE.sub(" ", cleaned)

    # Remove BPM hints.
    cleaned = _BPM_RE.sub(" ", cleaned)

    # Remove known tag words.
    all_tags = _ROUGH_TAGS | _FINAL_TAGS | _MIX_TAGS
    tokens = re.split(r"([\s_\-]+)", cleaned)
    filtered: list[str] = []
    for token in tokens:
        if token.strip().lower() not in all_tags:
            filtered.append(token)
    cleaned = "".join(filtered)

    # Strip trailing Unix timestamps / large export counters (8+ digits).
    cleaned = _TRAILING_DIGITS_RE.sub("", cleaned)

    # Collapse separators and trim.
    cleaned = cleaned.replace("_", " ").replace("-", " ")
    cleaned = _CLEANUP_RE.sub(" ", cleaned).strip()

    # Remove trailing/leading dashes or underscores that survived.
    cleaned = cleaned.strip(" -_")
    return cleaned


def parse_filename(file_path: str | Path) -> ParsedFilename:
    """Parse an audio filename for metadata hints.

    Args:
        file_path: Path to the audio file (only the filename is used).

    Returns:
        A ``ParsedFilename`` with best-effort extracted metadata.
    """
    path = Path(file_path)
    stem = path.stem  # filename without extension
    raw = path.name

    # --- Version ---
    version_match = _VERSION_RE.search(stem)
    version_hint = int(version_match.group(1)) if version_match else None

    # --- BPM ---
    bpm_match = _BPM_RE.search(stem)
    bpm_hint: int | None = None
    if bpm_match:
        raw_bpm = bpm_match.group(1) or bpm_match.group(2)
        if raw_bpm is not None:
            bpm_val = int(raw_bpm)
            # Sanity-check BPM range (40-300).
            if 40 <= bpm_val <= 300:
                bpm_hint = bpm_val

    # --- Tags ---
    tags = _extract_tags(stem)
    is_rough = bool(set(tags) & _ROUGH_TAGS)
    is_final = bool(set(tags) & _FINAL_TAGS)

    # --- Title ---
    title = _clean_title(stem)
    if not title:
        # Fallback: use the full stem if cleaning removed everything.
        title = stem

    return ParsedFilename(
        title=title,
        version_hint=version_hint,
        bpm_hint=bpm_hint,
        tags=tags,
        is_rough=is_rough,
        is_final=is_final,
        raw_filename=raw,
    )
