"""End-of-week/month summary data for the Manager agent.

Aggregates pipeline status, streak data, reputation changes, and upcoming
deadlines into a structured payload that the Manager uses to generate
an SMS summary to the artist.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from coordination.gamification.reputation import ReputationScore, ReputationTracker
from coordination.gamification.streaks import StreakData, StreakTracker
from coordination.state_machine import ReleaseState


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class UpcomingDeadline(BaseModel):
    """A release with an approaching deadline."""

    track_id: int
    title: str = ""
    release_date: datetime
    days_until: int
    current_state: str


class WeeklySummary(BaseModel):
    """Structured summary data for one week/month period."""

    period_start: datetime
    period_end: datetime
    period_type: str = Field(default="weekly", description="weekly or monthly")

    # Pipeline counts.
    tracks_in_progress: int = 0
    tracks_completed: int = 0
    tracks_released: int = 0
    tracks_stalled: int = 0

    # State breakdown.
    state_counts: dict[str, int] = Field(default_factory=dict)

    # Streak.
    streak: StreakData | None = None
    streak_message: str = ""

    # Reputation.
    reputation: ReputationScore | None = None
    reputation_change: int = 0
    rank: str = ""

    # Deadlines.
    upcoming_deadlines: list[UpcomingDeadline] = Field(default_factory=list)

    # Raw data for Manager template.
    highlights: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Summary generator
# ---------------------------------------------------------------------------

class WeeklySummaryGenerator:
    """Generates weekly/monthly summary data for the Manager agent.

    Parameters
    ----------
    streak_tracker:
        Artist's streak tracker.
    reputation_tracker:
        Artist's reputation tracker.
    """

    def __init__(
        self,
        streak_tracker: StreakTracker | None = None,
        reputation_tracker: ReputationTracker | None = None,
    ) -> None:
        self._streak_tracker = streak_tracker
        self._reputation_tracker = reputation_tracker

    def generate(
        self,
        tracked_releases: dict[int, tuple[ReleaseState, datetime]],
        release_titles: dict[int, str] | None = None,
        release_dates: dict[int, datetime] | None = None,
        previous_reputation_points: int = 0,
        now: datetime | None = None,
        period_type: str = "weekly",
    ) -> WeeklySummary:
        """Generate a summary for the current period.

        Parameters
        ----------
        tracked_releases:
            Mapping of track_id -> (current_state, entered_at).
        release_titles:
            Optional mapping of track_id -> title.
        release_dates:
            Optional mapping of track_id -> target release date.
        previous_reputation_points:
            Points at the start of the period (for calculating delta).
        now:
            Reference time.
        period_type:
            "weekly" or "monthly".
        """
        now = now or datetime.utcnow()
        release_titles = release_titles or {}
        release_dates = release_dates or {}

        # Period boundaries.
        if period_type == "monthly":
            period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if now.month == 12:
                period_end = now.replace(year=now.year + 1, month=1, day=1) - timedelta(seconds=1)
            else:
                period_end = now.replace(month=now.month + 1, day=1) - timedelta(seconds=1)
        else:
            # Weekly: Monday to Sunday.
            days_since_monday = now.weekday()
            period_start = (now - timedelta(days=days_since_monday)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            period_end = period_start + timedelta(days=7) - timedelta(seconds=1)

        # State counts.
        state_counts: dict[str, int] = {}
        in_progress = 0
        completed = 0
        released = 0
        stalled = 0

        terminal_states = {ReleaseState.RELEASED}
        complete_states = {ReleaseState.RELEASED, ReleaseState.UPLOADING}

        for track_id, (state, entered_at) in tracked_releases.items():
            state_counts[state.value] = state_counts.get(state.value, 0) + 1

            if state in terminal_states:
                released += 1
            elif state in complete_states:
                completed += 1
            else:
                in_progress += 1

            # Stalled: in a non-terminal state for >7 days.
            if state not in terminal_states and (now - entered_at).days > 7:
                stalled += 1

        # Upcoming deadlines.
        deadlines: list[UpcomingDeadline] = []
        for track_id, release_date in release_dates.items():
            if release_date > now:
                days_until = (release_date - now).days
                current_state = tracked_releases.get(track_id, (ReleaseState.DRAFT, now))[0]
                deadlines.append(
                    UpcomingDeadline(
                        track_id=track_id,
                        title=release_titles.get(track_id, f"Track {track_id}"),
                        release_date=release_date,
                        days_until=days_until,
                        current_state=current_state.value,
                    )
                )
        deadlines.sort(key=lambda d: d.release_date)

        # Streak.
        streak: StreakData | None = None
        streak_message = ""
        if self._streak_tracker:
            streak = self._streak_tracker.calculate(now)
            streak_message = self._streak_tracker.generate_message(streak)

        # Reputation.
        reputation: ReputationScore | None = None
        reputation_change = 0
        rank = ""
        if self._reputation_tracker:
            reputation = self._reputation_tracker.calculate()
            reputation_change = reputation.total_points - previous_reputation_points
            rank = self._reputation_tracker.get_rank(reputation)

        # Highlights.
        highlights = self._generate_highlights(
            in_progress=in_progress,
            completed=completed,
            released=released,
            stalled=stalled,
            streak=streak,
            reputation_change=reputation_change,
            deadlines=deadlines,
        )

        return WeeklySummary(
            period_start=period_start,
            period_end=period_end,
            period_type=period_type,
            tracks_in_progress=in_progress,
            tracks_completed=completed,
            tracks_released=released,
            tracks_stalled=stalled,
            state_counts=state_counts,
            streak=streak,
            streak_message=streak_message,
            reputation=reputation,
            reputation_change=reputation_change,
            rank=rank,
            upcoming_deadlines=deadlines,
            highlights=highlights,
        )

    @staticmethod
    def _generate_highlights(
        in_progress: int,
        completed: int,
        released: int,
        stalled: int,
        streak: StreakData | None,
        reputation_change: int,
        deadlines: list[UpcomingDeadline],
    ) -> list[str]:
        """Generate human-readable highlight lines."""
        highlights: list[str] = []

        if released > 0:
            highlights.append(f"{released} track(s) released this period")
        if in_progress > 0:
            highlights.append(f"{in_progress} track(s) in progress")
        if stalled > 0:
            highlights.append(f"{stalled} track(s) stalled (>7 days without progress)")

        if streak and streak.streak_active and streak.current_streak_weeks >= 2:
            highlights.append(f"{streak.current_streak_weeks}-week upload streak active")
        elif streak and not streak.streak_active and streak.gap_weeks > 0:
            highlights.append(f"Upload gap: {streak.gap_weeks} week(s)")

        if reputation_change > 0:
            highlights.append(f"Reputation: +{reputation_change} points")
        elif reputation_change < 0:
            highlights.append(f"Reputation: {reputation_change} points")

        urgent = [d for d in deadlines if d.days_until <= 7]
        if urgent:
            for d in urgent:
                highlights.append(f"Deadline in {d.days_until} day(s): {d.title}")

        return highlights
