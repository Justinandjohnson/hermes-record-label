"""Creation cadence tracking — streaks and upload frequency.

An artist maintains a streak by uploading at least one track per week.
The streak tracker calculates current streak length, longest streak,
gap detection, and generates appropriate motivational or re-engagement
messages.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field


class StreakData(BaseModel):
    """Snapshot of an artist's streak state."""

    artist_id: int | None = None
    current_streak_weeks: int = 0
    longest_streak_weeks: int = 0
    total_uploads: int = 0
    last_upload_date: datetime | None = None
    streak_active: bool = False
    gap_weeks: int = 0
    weekly_uploads: dict[str, int] = Field(
        default_factory=dict,
        description="ISO week string (YYYY-WNN) -> upload count",
    )


class StreakTracker:
    """Tracks upload cadence and streaks for a single artist.

    A week starts on Monday.  An upload any time during the week counts.
    """

    def __init__(self, artist_id: int | None = None) -> None:
        self.artist_id = artist_id
        self._uploads: list[datetime] = []

    def record_upload(self, uploaded_at: datetime | None = None) -> None:
        """Record a track upload."""
        ts = uploaded_at or datetime.now(UTC)
        self._uploads.append(ts)
        self._uploads.sort()

    def load_uploads(self, timestamps: list[datetime]) -> None:
        """Bulk-load upload timestamps (e.g. from database)."""
        self._uploads = sorted(timestamps)

    @staticmethod
    def _iso_week(dt: datetime) -> str:
        """Return ISO week string like '2026-W20'."""
        iso = dt.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    @staticmethod
    def _week_start(dt: datetime) -> datetime:
        """Return Monday 00:00 of the week containing dt."""
        iso = dt.isocalendar()
        monday = datetime.fromisocalendar(iso[0], iso[1], 1)
        return monday.replace(hour=0, minute=0, second=0, microsecond=0)

    def _weekly_upload_counts(self) -> dict[str, int]:
        """Count uploads per ISO week."""
        counts: dict[str, int] = {}
        for ts in self._uploads:
            week = self._iso_week(ts)
            counts[week] = counts.get(week, 0) + 1
        return counts

    def calculate(self, now: datetime | None = None) -> StreakData:
        """Calculate current streak data.

        Parameters
        ----------
        now:
            Reference time for "current week" (defaults to utcnow).

        Returns
        -------
        StreakData with all computed fields.
        """
        now = now or datetime.now(UTC)
        weekly = self._weekly_upload_counts()

        if not self._uploads:
            return StreakData(
                artist_id=self.artist_id,
                weekly_uploads=weekly,
            )

        current_week = self._iso_week(now)

        # Calculate streaks by walking backwards from current week.
        current_streak = 0
        longest_streak = 0
        running = 0

        # Generate all weeks from first upload to now.
        first_monday = self._week_start(self._uploads[0])
        current_monday = self._week_start(now)

        all_weeks: list[str] = []
        cursor = first_monday
        while cursor <= current_monday:
            all_weeks.append(self._iso_week(cursor))
            cursor += timedelta(weeks=1)

        # Walk forward, track consecutive weeks with uploads.
        for week in all_weeks:
            if week in weekly:
                running += 1
                longest_streak = max(longest_streak, running)
            else:
                running = 0

        # Current streak: walk backwards from current week.
        current_streak = 0
        for week in reversed(all_weeks):
            if week in weekly:
                current_streak += 1
            else:
                break

        # Gap: weeks since last upload.
        last_upload = self._uploads[-1]
        last_upload_week_start = self._week_start(last_upload)
        current_week_start = self._week_start(now)
        gap_weeks = max(0, int((current_week_start - last_upload_week_start).days / 7) - 1)
        if current_week not in weekly:
            gap_weeks = max(gap_weeks, 1)

        streak_active = current_streak > 0 and current_week in weekly

        # Also count as active if the previous week had an upload and we're
        # still in the current week (grace: haven't missed yet).
        if not streak_active and current_streak > 0:
            prev_week_monday = current_monday - timedelta(weeks=1)
            prev_week = self._iso_week(prev_week_monday)
            if prev_week in weekly:
                streak_active = True
                gap_weeks = 0

        return StreakData(
            artist_id=self.artist_id,
            current_streak_weeks=current_streak,
            longest_streak_weeks=longest_streak,
            total_uploads=len(self._uploads),
            last_upload_date=self._uploads[-1] if self._uploads else None,
            streak_active=streak_active,
            gap_weeks=gap_weeks,
            weekly_uploads=weekly,
        )

    def generate_message(self, streak: StreakData | None = None) -> str:
        """Generate an appropriate motivational or re-engagement message."""
        if streak is None:
            streak = self.calculate()

        if streak.total_uploads == 0:
            return "No uploads yet — drop your first track to start your streak!"

        if streak.streak_active and streak.current_streak_weeks >= 4:
            return (
                f"You're on a {streak.current_streak_weeks}-week streak! "
                f"That's serious momentum. Keep feeding the catalog."
            )

        if streak.streak_active:
            return (
                f"{streak.current_streak_weeks}-week streak and counting. "
                f"Keep it rolling."
            )

        if streak.gap_weeks == 1:
            return (
                "You missed last week — no sweat. "
                "Drop something this week to keep your streak alive."
            )

        if streak.gap_weeks <= 3:
            return (
                f"It's been {streak.gap_weeks} weeks since your last upload. "
                f"Your {streak.longest_streak_weeks}-week record is waiting to be broken."
            )

        return (
            f"Been quiet for {streak.gap_weeks} weeks. "
            f"Your catalog misses you. Even a rough demo counts."
        )
