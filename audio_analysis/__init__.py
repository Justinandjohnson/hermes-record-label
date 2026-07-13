"""Audio analysis pipeline using Gemini 3.1 Pro multimodal capabilities."""

from .analyzer import AnalyzerError, analyze, analyze_async
from .gemini_client import (
    GeminiClientError,
    UnsupportedFormatError,
    FileTooLargeError,
    AnalysisParseError,
)
from .memory_builder import (
    build_memory,
    get_artist_patterns,
    get_evolution_arc,
    get_strengths_and_weaknesses,
    get_track_context,
)
from .models import (
    ArtistPatterns,
    AudioAnalysis,
    AudioMemoryEntry,
    EnergyCurvePoint,
    MemoryCategory,
    MixObservation,
    NotableMoment,
    TrackContext,
)

__all__ = [
    "analyze",
    "analyze_async",
    "AnalyzerError",
    "GeminiClientError",
    "UnsupportedFormatError",
    "FileTooLargeError",
    "AnalysisParseError",
    "build_memory",
    "get_artist_patterns",
    "get_evolution_arc",
    "get_strengths_and_weaknesses",
    "get_track_context",
    "ArtistPatterns",
    "AudioAnalysis",
    "AudioMemoryEntry",
    "EnergyCurvePoint",
    "MemoryCategory",
    "MixObservation",
    "NotableMoment",
    "TrackContext",
]
