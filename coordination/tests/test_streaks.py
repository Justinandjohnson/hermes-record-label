"""Tests for streak tracking and cadence gamification."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from coordination.gamification.streaks import StreakData, StreakTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _monday(weeks_ago: int = 0) -> datetime:
    """Return a Monday N weeks ago from a fixed reference."""
    ref = datetime(2026, 5, 11)  # A Monday
    return ref - timedelta(weeks=weeks_ago)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEmptyTracker:
    def test_no_uploads(self) -> None:
        tracker = StreakTracker(artist_id=1)
        data = tracker.calculate()
        assert data.current_streak_weeks == 0
        assert data.longest_streak_weeks == 0
        assert data.total_uploads == 0
        assert data.streak_active is False

    def test_empty_message(self) -> None:
        tracker = StreakTracker()
        msg = tracker.generate_message()
        assert "first track" in msg.lower()


class TestSingleUpload:
    def test_one_upload_current_week(self) -> None:
        tracker = StreakTracker(artist_id=1)
        now = _monday(0) + timedelta(days=2)  # Wednesday of current week
        tracker.record_upload(now - timedelta(hours=1))

        data = tracker.calculate(now)
        assert data.current_streak_weeks == 1
        assert data.total_uploads == 1
        assert data.streak_active is True


class TestConsecutiveWeeks:
    def test_three_week_streak(self) -> None:
        tracker = StreakTracker(artist_id=1)
        now = _monday(0) + timedelta(days=3)

        # Uploads on three consecutive weeks.
        tracker.record_upload(_monday(2) + timedelta(days=1))
        tracker.record_upload(_monday(1) + timedelta(days=3))
        tracker.record_upload(_monday(0) + timedelta(days=2))

        data = tracker.calculate(now)
        assert data.current_streak_weeks == 3
        assert data.longest_streak_weeks == 3
        assert data.streak_active is True

    def test_broken_streak(self) -> None:
        tracker = StreakTracker(artist_id=1)
        now = _monday(0) + timedelta(days=3)

        # Week 4, week 3, skip week 2, week 1, week 0.
        tracker.record_upload(_monday(4) + timedelta(days=1))
        tracker.record_upload(_monday(3) + timedelta(days=1))
        # gap in week 2
        tracker.record_upload(_monday(1) + timedelta(days=1))
        tracker.record_upload(_monday(0) + timedelta(days=1))

        data = tracker.calculate(now)
        assert data.current_streak_weeks == 2  # Only week 0 + week 1
        assert data.longest_streak_weeks == 2  # Week 3 + week 4 also 2

    def test_multiple_uploads_per_week_count_as_one(self) -> None:
        tracker = StreakTracker(artist_id=1)
        now = _monday(0) + timedelta(days=5)

        tracker.record_upload(_monday(0) + timedelta(days=1))
        tracker.record_upload(_monday(0) + timedelta(days=3))
        tracker.record_upload(_monday(0) + timedelta(days=4))

        data = tracker.calculate(now)
        assert data.current_streak_weeks == 1
        assert data.total_uploads == 3


class TestGapDetection:
    def test_gap_weeks_calculated(self) -> None:
        tracker = StreakTracker(artist_id=1)
        now = _monday(0) + timedelta(days=3)

        # Last upload was 3 weeks ago.
        tracker.record_upload(_monday(3) + timedelta(days=2))

        data = tracker.calculate(now)
        assert data.gap_weeks >= 2
        assert data.streak_active is False

    def test_no_gap_when_active(self) -> None:
        tracker = StreakTracker(artist_id=1)
        now = _monday(0) + timedelta(days=2)
        tracker.record_upload(now - timedelta(hours=5))

        data = tracker.calculate(now)
        assert data.gap_weeks == 0


class TestStreakMessages:
    def test_long_streak_message(self) -> None:
        tracker = StreakTracker(artist_id=1)
        data = StreakData(
            current_streak_weeks=5,
            longest_streak_weeks=5,
            total_uploads=5,
            streak_active=True,
        )
        msg = tracker.generate_message(data)
        assert "5-week streak" in msg
        assert "momentum" in msg.lower()

    def test_short_streak_message(self) -> None:
        tracker = StreakTracker()
        data = StreakData(
            current_streak_weeks=2,
            total_uploads=2,
            streak_active=True,
        )
        msg = tracker.generate_message(data)
        assert "2-week streak" in msg

    def test_one_week_gap_message(self) -> None:
        tracker = StreakTracker()
        data = StreakData(
            current_streak_weeks=0,
            total_uploads=3,
            streak_active=False,
            gap_weeks=1,
        )
        msg = tracker.generate_message(data)
        assert "missed" in msg.lower()

    def test_long_gap_message(self) -> None:
        tracker = StreakTracker()
        data = StreakData(
            current_streak_weeks=0,
            total_uploads=5,
            streak_active=False,
            gap_weeks=5,
        )
        msg = tracker.generate_message(data)
        assert "5 weeks" in msg


class TestBulkLoad:
    def test_load_uploads(self) -> None:
        tracker = StreakTracker(artist_id=1)
        timestamps = [
            _monday(3) + timedelta(days=1),
            _monday(2) + timedelta(days=2),
            _monday(1) + timedelta(days=3),
        ]
        tracker.load_uploads(timestamps)
        data = tracker.calculate(_monday(1) + timedelta(days=5))
        assert data.total_uploads == 3
        assert data.current_streak_weeks == 3


class TestWeeklyUploadCounts:
    def test_weekly_counts_populated(self) -> None:
        tracker = StreakTracker()
        tracker.record_upload(_monday(1) + timedelta(days=1))
        tracker.record_upload(_monday(1) + timedelta(days=3))
        tracker.record_upload(_monday(0) + timedelta(days=2))

        data = tracker.calculate(_monday(0) + timedelta(days=4))
        assert len(data.weekly_uploads) == 2
        # The week with 2 uploads.
        assert any(v == 2 for v in data.weekly_uploads.values())
