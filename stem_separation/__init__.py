"""Stem separation and lyrics extraction for AI Record Label.

Entry points:
    separate_stems(file_path, output_dir) -> dict[str, str]
    extract_lyrics(vocal_path, api_key) -> LyricsResult
"""

from .separator import StemSeparatorError, separate_stems
from .lyrics_extractor import LyricsResult, extract_lyrics
from .mumble_analyzer import MumbleAnalysis, analyze_mumble

__all__ = [
    "separate_stems", "StemSeparatorError",
    "extract_lyrics", "LyricsResult",
    "analyze_mumble", "MumbleAnalysis",
]
