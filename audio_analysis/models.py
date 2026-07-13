"""Pydantic V2 models for audio analysis and memory."""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field


class EnergyCurvePoint(BaseModel):
    """A single point on the track's energy curve."""

    timestamp: str = Field(description="Timestamp in mm:ss or hh:mm:ss format")
    energy_level: float = Field(ge=0.0, le=1.0, description="Energy 0.0 (silent) to 1.0 (peak)")


class MixObservation(BaseModel):
    """A mix/production observation tied to a specific moment."""

    timestamp: str
    observation: str


class NotableMoment(BaseModel):
    """A standout moment in the track -- good or bad."""

    timestamp: str
    description: str
    quality_judgment: str = Field(description="e.g. 'strength', 'weakness', 'interesting', 'needs_work'")


class AudioAnalysis(BaseModel):
    """Full structured analysis of a single audio track from Gemini 3.1 Pro."""

    track_id: int | None = None
    model_used: str = "gemini-3.1-pro-preview"
    bpm: float | None = None
    musical_key: str | None = None
    energy_curve: list[EnergyCurvePoint] = Field(default_factory=list)
    structure: dict[str, str] = Field(
        default_factory=dict,
        description='Map of section name to timestamp range, e.g. {"intro": "0:00-0:15"}',
    )
    instruments: list[str] = Field(default_factory=list)
    genre_tags: list[str] = Field(default_factory=list)
    mood_tags: list[str] = Field(default_factory=list)
    mix_observations: list[MixObservation] = Field(default_factory=list)
    notable_moments: list[NotableMoment] = Field(default_factory=list)
    raw_response: str | None = None


class MemoryCategory(str, enum.Enum):
    """Categories for audio memory entries."""

    SIGNATURE_SOUND = "signature_sound"
    RECURRING_STRENGTH = "recurring_strength"
    RECURRING_WEAKNESS = "recurring_weakness"
    GENRE_TENDENCY = "genre_tendency"
    PRODUCTION_PATTERN = "production_pattern"
    ARRANGEMENT_HABIT = "arrangement_habit"
    ENERGY_PREFERENCE = "energy_preference"
    INSTRUMENT_PALETTE = "instrument_palette"
    EVOLUTION_NOTE = "evolution_note"


class AudioMemoryEntry(BaseModel):
    """A single learned observation about the artist's sound."""

    id: int | None = None
    category: MemoryCategory
    observation: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    first_noticed_track_id: int | None = None
    supporting_track_ids: list[int] = Field(default_factory=list)
    times_observed: int = 1
    last_updated: datetime | None = None
    created_at: datetime | None = None


class TrackContext(BaseModel):
    """How a track relates to the artist's catalog."""

    track_id: int
    similarities_to_past: list[str] = Field(default_factory=list)
    departures_from_past: list[str] = Field(default_factory=list)
    evolution_notes: list[str] = Field(default_factory=list)
    confirmed_patterns: list[str] = Field(default_factory=list)
    new_observations: list[str] = Field(default_factory=list)


class ArtistPatterns(BaseModel):
    """All audio memory entries sorted by confidence."""

    signature_sounds: list[AudioMemoryEntry] = Field(default_factory=list)
    strengths: list[AudioMemoryEntry] = Field(default_factory=list)
    weaknesses: list[AudioMemoryEntry] = Field(default_factory=list)
    production_patterns: list[AudioMemoryEntry] = Field(default_factory=list)
    genre_tendencies: list[AudioMemoryEntry] = Field(default_factory=list)
    evolution_notes: list[AudioMemoryEntry] = Field(default_factory=list)
    all_entries: list[AudioMemoryEntry] = Field(default_factory=list)
