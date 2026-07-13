"""Tests for reputation scoring."""

from __future__ import annotations

import pytest

from coordination.gamification.reputation import (
    POINTS_MISSED_DEADLINE,
    POINTS_RELEASED_ON_SCHEDULE,
    POINTS_STREAK_WEEK,
    POINTS_TRACK_COMPLETED,
    ReputationEvent,
    ReputationTracker,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPointValues:
    def test_track_completed_points(self) -> None:
        assert POINTS_TRACK_COMPLETED == 10

    def test_on_schedule_points(self) -> None:
        assert POINTS_RELEASED_ON_SCHEDULE == 20

    def test_missed_deadline_points(self) -> None:
        assert POINTS_MISSED_DEADLINE == -5

    def test_streak_week_points(self) -> None:
        assert POINTS_STREAK_WEEK == 5


class TestEmptyTracker:
    def test_zero_score(self) -> None:
        tracker = ReputationTracker(artist_id=1)
        score = tracker.calculate()
        assert score.total_points == 0
        assert score.tracks_completed == 0
        assert score.completion_rate == 0.0


class TestRecordEvents:
    def test_track_completed(self) -> None:
        tracker = ReputationTracker(artist_id=1)
        event = tracker.record_track_completed(track_id=1)
        assert event.points == 10
        assert event.event_type == "track_completed"

        score = tracker.calculate()
        assert score.total_points == 10
        assert score.tracks_completed == 1

    def test_released_on_schedule(self) -> None:
        tracker = ReputationTracker(artist_id=1)
        tracker.record_track_completed(track_id=1)
        tracker.record_released_on_schedule(track_id=1)

        score = tracker.calculate()
        assert score.total_points == 30  # 10 + 20
        assert score.releases_on_schedule == 1

    def test_missed_deadline(self) -> None:
        tracker = ReputationTracker(artist_id=1)
        tracker.record_track_completed(track_id=1)
        tracker.record_missed_deadline(track_id=2)

        score = tracker.calculate()
        assert score.total_points == 5  # 10 - 5
        assert score.missed_deadlines == 1

    def test_streak_weeks(self) -> None:
        tracker = ReputationTracker(artist_id=1)
        for _ in range(4):
            tracker.record_streak_week()

        score = tracker.calculate()
        assert score.total_points == 20  # 4 * 5
        assert score.streak_weeks_earned == 4

    def test_points_floor_at_zero(self) -> None:
        tracker = ReputationTracker(artist_id=1)
        for _ in range(10):
            tracker.record_missed_deadline(track_id=1)

        score = tracker.calculate()
        assert score.total_points == 0  # Floored at 0


class TestCompletionRate:
    def test_completion_rate(self) -> None:
        tracker = ReputationTracker(artist_id=1)
        tracker.record_track_completed(track_id=1)
        tracker.record_track_completed(track_id=2)
        tracker._total_projects = 4  # 2 completed out of 4 started

        score = tracker.calculate()
        assert score.completion_rate == 50.0

    def test_100_percent_completion(self) -> None:
        tracker = ReputationTracker(artist_id=1)
        tracker.record_track_completed(track_id=1)
        tracker.record_track_completed(track_id=2)

        score = tracker.calculate()
        assert score.completion_rate == 100.0


class TestRanks:
    @pytest.mark.parametrize(
        ("points_from_completions", "expected_rank"),
        [
            (0, "Unsigned"),
            (2, "Bronze"),
            (5, "Silver"),
            (10, "Gold"),
            (20, "Platinum"),
        ],
    )
    def test_ranks(self, points_from_completions: int, expected_rank: str) -> None:
        tracker = ReputationTracker(artist_id=1)
        for i in range(points_from_completions):
            tracker.record_track_completed(track_id=i + 1)
        rank = tracker.get_rank()
        assert rank == expected_rank


class TestSummary:
    def test_summary_structure(self) -> None:
        tracker = ReputationTracker(artist_id=1)
        tracker.record_track_completed(track_id=1)
        tracker.record_released_on_schedule(track_id=1)
        tracker.record_streak_week()

        summary = tracker.generate_summary()
        assert "rank" in summary
        assert "total_points" in summary
        assert "tracks_completed" in summary
        assert "on_schedule_rate" in summary
        assert "streak_weeks" in summary
        assert summary["total_points"] == 35  # 10 + 20 + 5


class TestBulkLoad:
    def test_load_events(self) -> None:
        events = [
            ReputationEvent(event_type="track_completed", points=10, track_id=1),
            ReputationEvent(event_type="released_on_schedule", points=20, track_id=1),
            ReputationEvent(event_type="streak_week", points=5),
        ]
        tracker = ReputationTracker(artist_id=1)
        tracker.load_events(events)

        score = tracker.calculate()
        assert score.total_points == 35
        assert score.tracks_completed == 1
