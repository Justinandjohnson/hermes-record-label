"""Label reputation scoring.

Tracks cumulative reputation points based on artist activity:
- Track completed: +10
- Released on schedule: +20
- Missed deadline: -5
- Streak weeks: +5 per active week
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

POINTS_TRACK_COMPLETED = 10
POINTS_RELEASED_ON_SCHEDULE = 20
POINTS_MISSED_DEADLINE = -5
POINTS_STREAK_WEEK = 5


class ReputationEvent(BaseModel):
    """A single reputation-affecting event."""

    event_type: str = Field(description="track_completed, released_on_schedule, missed_deadline, streak_week")
    points: int
    track_id: int | None = None
    description: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ReputationScore(BaseModel):
    """Current reputation summary."""

    artist_id: int | None = None
    total_points: int = 0
    tracks_completed: int = 0
    releases_on_schedule: int = 0
    missed_deadlines: int = 0
    streak_weeks_earned: int = 0
    completion_rate: float = 0.0
    events: list[ReputationEvent] = Field(default_factory=list)


class ReputationTracker:
    """Tracks and calculates label reputation for a single artist."""

    def __init__(self, artist_id: int | None = None) -> None:
        self.artist_id = artist_id
        self._events: list[ReputationEvent] = []
        self._total_projects: int = 0

    def record_track_completed(self, track_id: int, description: str = "") -> ReputationEvent:
        """Award points for completing a track (reaching RELEASED)."""
        event = ReputationEvent(
            event_type="track_completed",
            points=POINTS_TRACK_COMPLETED,
            track_id=track_id,
            description=description or f"Track {track_id} completed",
        )
        self._events.append(event)
        self._total_projects += 1
        return event

    def record_released_on_schedule(self, track_id: int, description: str = "") -> ReputationEvent:
        """Bonus for releasing on or before the target date."""
        event = ReputationEvent(
            event_type="released_on_schedule",
            points=POINTS_RELEASED_ON_SCHEDULE,
            track_id=track_id,
            description=description or f"Track {track_id} released on schedule",
        )
        self._events.append(event)
        return event

    def record_missed_deadline(self, track_id: int, description: str = "") -> ReputationEvent:
        """Deduct points for missing a release deadline."""
        event = ReputationEvent(
            event_type="missed_deadline",
            points=POINTS_MISSED_DEADLINE,
            track_id=track_id,
            description=description or f"Track {track_id} missed deadline",
        )
        self._events.append(event)
        return event

    def record_streak_week(self, description: str = "") -> ReputationEvent:
        """Award points for an active streak week."""
        event = ReputationEvent(
            event_type="streak_week",
            points=POINTS_STREAK_WEEK,
            description=description or "Active streak week",
        )
        self._events.append(event)
        return event

    def load_events(self, events: list[ReputationEvent]) -> None:
        """Bulk-load events (e.g. from database)."""
        self._events = list(events)
        self._total_projects = sum(
            1 for e in events if e.event_type == "track_completed"
        )

    def calculate(self) -> ReputationScore:
        """Calculate the current reputation score."""
        total_points = sum(e.points for e in self._events)
        tracks_completed = sum(1 for e in self._events if e.event_type == "track_completed")
        on_schedule = sum(1 for e in self._events if e.event_type == "released_on_schedule")
        missed = sum(1 for e in self._events if e.event_type == "missed_deadline")
        streak_weeks = sum(1 for e in self._events if e.event_type == "streak_week")

        # Completion rate: tracks completed / total projects started.
        completion_rate = 0.0
        if self._total_projects > 0:
            completion_rate = round(tracks_completed / self._total_projects * 100, 1)

        return ReputationScore(
            artist_id=self.artist_id,
            total_points=max(0, total_points),  # Floor at 0.
            tracks_completed=tracks_completed,
            releases_on_schedule=on_schedule,
            missed_deadlines=missed,
            streak_weeks_earned=streak_weeks,
            completion_rate=completion_rate,
            events=list(self._events),
        )

    def get_rank(self, score: ReputationScore | None = None) -> str:
        """Return a descriptive rank based on points."""
        if score is None:
            score = self.calculate()

        points = score.total_points
        if points >= 200:
            return "Platinum"
        if points >= 100:
            return "Gold"
        if points >= 50:
            return "Silver"
        if points >= 20:
            return "Bronze"
        return "Unsigned"

    def generate_summary(self, score: ReputationScore | None = None) -> dict[str, Any]:
        """Generate a summary dict for the Manager agent."""
        if score is None:
            score = self.calculate()

        return {
            "rank": self.get_rank(score),
            "total_points": score.total_points,
            "tracks_completed": score.tracks_completed,
            "on_schedule_rate": (
                round(score.releases_on_schedule / score.tracks_completed * 100, 1)
                if score.tracks_completed > 0
                else 0.0
            ),
            "streak_weeks": score.streak_weeks_earned,
            "missed_deadlines": score.missed_deadlines,
            "completion_rate": score.completion_rate,
        }
