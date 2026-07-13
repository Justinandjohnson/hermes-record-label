"""Tests for the release state machine."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from coordination.state_machine import (
    InvalidTransition,
    ReleaseState,
    ReleaseStateMachine,
    StateTransitionLog,
    TIMEOUT_RULES,
    TRANSITIONS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def machine() -> ReleaseStateMachine:
    return ReleaseStateMachine(track_id=1)


@pytest.fixture
def transition_log() -> list[StateTransitionLog]:
    """Accumulator for on_transition callbacks."""
    log: list[StateTransitionLog] = []
    return log


@pytest.fixture
def machine_with_callback(transition_log: list[StateTransitionLog]) -> ReleaseStateMachine:
    return ReleaseStateMachine(
        track_id=1,
        initial_state=ReleaseState.DRAFT,
        on_transition=transition_log.append,
    )


# ---------------------------------------------------------------------------
# Basic state tests
# ---------------------------------------------------------------------------

class TestBasicState:
    def test_initial_state_is_draft(self, machine: ReleaseStateMachine) -> None:
        assert machine.state == ReleaseState.DRAFT

    def test_custom_initial_state(self) -> None:
        m = ReleaseStateMachine(track_id=2, initial_state=ReleaseState.IN_REVIEW)
        assert m.state == ReleaseState.IN_REVIEW

    def test_track_id(self, machine: ReleaseStateMachine) -> None:
        assert machine.track_id == 1

    def test_empty_history(self, machine: ReleaseStateMachine) -> None:
        assert machine.history == []


# ---------------------------------------------------------------------------
# Transition tests — happy path
# ---------------------------------------------------------------------------

class TestTransitions:
    def test_draft_to_in_review(self, machine: ReleaseStateMachine) -> None:
        log = machine.transition(trigger="new_track_detected", changed_by="a_and_r")
        assert machine.state == ReleaseState.IN_REVIEW
        assert log.from_state == ReleaseState.DRAFT
        assert log.to_state == ReleaseState.IN_REVIEW
        assert log.changed_by == "a_and_r"

    def test_in_review_to_feedback_given(self, machine: ReleaseStateMachine) -> None:
        machine.transition(trigger="new_track_detected")
        log = machine.transition(trigger="audio_analysis_complete")
        assert machine.state == ReleaseState.FEEDBACK_GIVEN
        assert log.from_state == ReleaseState.IN_REVIEW

    def test_feedback_given_to_approved(self, machine: ReleaseStateMachine) -> None:
        machine.transition(trigger="new_track_detected")
        machine.transition(trigger="audio_analysis_complete")
        log = machine.transition(trigger="artist_approves")
        assert machine.state == ReleaseState.APPROVED
        assert log.from_state == ReleaseState.FEEDBACK_GIVEN

    def test_feedback_given_to_draft_revision(self, machine: ReleaseStateMachine) -> None:
        machine.transition(trigger="new_track_detected")
        machine.transition(trigger="audio_analysis_complete")
        machine.transition(trigger="revision_uploaded")
        assert machine.state == ReleaseState.DRAFT

    def test_approved_to_art_needed(self, machine: ReleaseStateMachine) -> None:
        machine.transition(trigger="new_track_detected")
        machine.transition(trigger="audio_analysis_complete")
        machine.transition(trigger="artist_approves")
        # Chain transition: APPROVED -> ART_NEEDED
        machine.transition(trigger="artist_approves")
        assert machine.state == ReleaseState.ART_NEEDED

    def test_art_submitted_to_approved(self, machine: ReleaseStateMachine) -> None:
        m = ReleaseStateMachine(track_id=1, initial_state=ReleaseState.ART_NEEDED)
        m.transition(trigger="artwork_submitted")
        assert m.state == ReleaseState.ART_SUBMITTED
        m.transition(trigger="art_approved")
        assert m.state == ReleaseState.ART_APPROVED

    def test_art_submitted_to_rejected(self, machine: ReleaseStateMachine) -> None:
        m = ReleaseStateMachine(track_id=1, initial_state=ReleaseState.ART_NEEDED)
        m.transition(trigger="artwork_submitted")
        m.transition(trigger="art_rejected")
        assert m.state == ReleaseState.ART_NEEDED

    def test_full_happy_path(self) -> None:
        """Walk the entire pipeline from DRAFT to RELEASED."""
        m = ReleaseStateMachine(track_id=99)
        m.transition(trigger="new_track_detected")           # DRAFT -> IN_REVIEW
        m.transition(trigger="audio_analysis_complete")       # IN_REVIEW -> FEEDBACK_GIVEN
        m.transition(trigger="artist_approves")               # FEEDBACK_GIVEN -> APPROVED
        m.transition(trigger="artist_approves")               # APPROVED -> ART_NEEDED
        m.transition(trigger="artwork_submitted")             # ART_NEEDED -> ART_SUBMITTED
        m.transition(trigger="art_approved")                  # ART_SUBMITTED -> ART_APPROVED
        m.transition(trigger="release_date_set")              # ART_APPROVED -> RELEASE_READY
        m.transition(trigger="release_date_set")              # RELEASE_READY -> PREFLIGHT
        m.transition(trigger="preflight_passed")              # PREFLIGHT -> UPLOADING
        m.transition(trigger="upload_completed")              # UPLOADING -> RELEASED
        assert m.state == ReleaseState.RELEASED
        assert len(m.history) == 10

    def test_preflight_failed_missing_cover(self) -> None:
        m = ReleaseStateMachine(track_id=1, initial_state=ReleaseState.PREFLIGHT)
        m.transition(trigger="preflight_failed", guard="missing_cover")
        assert m.state == ReleaseState.ART_NEEDED

    def test_preflight_failed_bad_metadata(self) -> None:
        m = ReleaseStateMachine(track_id=1, initial_state=ReleaseState.PREFLIGHT)
        m.transition(trigger="preflight_failed", guard="bad_metadata")
        assert m.state == ReleaseState.FEEDBACK_GIVEN

    def test_upload_failed_retries(self) -> None:
        m = ReleaseStateMachine(track_id=1, initial_state=ReleaseState.UPLOADING)
        m.transition(trigger="upload_failed")
        assert m.state == ReleaseState.RELEASE_READY


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------

class TestInvalidTransitions:
    def test_cannot_go_from_draft_to_approved(self, machine: ReleaseStateMachine) -> None:
        with pytest.raises(InvalidTransition):
            machine.transition(trigger="artist_approves")

    def test_cannot_go_from_released_to_uploading(self) -> None:
        m = ReleaseStateMachine(track_id=1, initial_state=ReleaseState.RELEASED)
        with pytest.raises(InvalidTransition):
            m.transition(trigger="preflight_passed")

    def test_invalid_trigger_name(self, machine: ReleaseStateMachine) -> None:
        with pytest.raises(InvalidTransition):
            machine.transition(trigger="nonexistent_event")


# ---------------------------------------------------------------------------
# Redo (any state -> DRAFT)
# ---------------------------------------------------------------------------

class TestRedo:
    @pytest.mark.parametrize(
        "state",
        [s for s in ReleaseState if s != ReleaseState.DRAFT],
    )
    def test_redo_from_any_state(self, state: ReleaseState) -> None:
        m = ReleaseStateMachine(track_id=1, initial_state=state)
        m.transition(trigger="artist_requests_redo")
        assert m.state == ReleaseState.DRAFT


# ---------------------------------------------------------------------------
# Callback
# ---------------------------------------------------------------------------

class TestCallback:
    def test_on_transition_callback_fires(
        self,
        machine_with_callback: ReleaseStateMachine,
        transition_log: list[StateTransitionLog],
    ) -> None:
        machine_with_callback.transition(trigger="new_track_detected")
        assert len(transition_log) == 1
        assert transition_log[0].to_state == ReleaseState.IN_REVIEW


# ---------------------------------------------------------------------------
# Force state
# ---------------------------------------------------------------------------

class TestForceState:
    def test_force_state_bypasses_guards(self, machine: ReleaseStateMachine) -> None:
        machine.force_state(ReleaseState.RELEASED, changed_by="admin", reason="Testing")
        assert machine.state == ReleaseState.RELEASED
        assert "FORCED" in machine.history[-1].reason


# ---------------------------------------------------------------------------
# can_transition
# ---------------------------------------------------------------------------

class TestCanTransition:
    def test_valid_transition(self, machine: ReleaseStateMachine) -> None:
        assert machine.can_transition("new_track_detected") is True

    def test_invalid_transition(self, machine: ReleaseStateMachine) -> None:
        assert machine.can_transition("upload_completed") is False

    def test_guarded_transition(self) -> None:
        m = ReleaseStateMachine(track_id=1, initial_state=ReleaseState.PREFLIGHT)
        assert m.can_transition("preflight_failed", guard="missing_cover") is True
        assert m.can_transition("preflight_failed", guard="nonexistent") is False


# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------

class TestTimeouts:
    def test_feedback_stale_timeout(self) -> None:
        m = ReleaseStateMachine(track_id=1, initial_state=ReleaseState.FEEDBACK_GIVEN)
        entered_at = datetime.utcnow() - timedelta(days=8)
        fired = m.get_applicable_timeouts(entered_at)
        assert len(fired) == 1
        assert fired[0].timeout_event == "timeout_feedback_stale"

    def test_no_timeout_within_window(self) -> None:
        m = ReleaseStateMachine(track_id=1, initial_state=ReleaseState.FEEDBACK_GIVEN)
        entered_at = datetime.utcnow() - timedelta(days=3)
        fired = m.get_applicable_timeouts(entered_at)
        assert len(fired) == 0

    def test_art_overdue_timeout(self) -> None:
        m = ReleaseStateMachine(track_id=1, initial_state=ReleaseState.ART_NEEDED)
        entered_at = datetime.utcnow() - timedelta(days=4)
        fired = m.get_applicable_timeouts(entered_at)
        assert len(fired) == 1
        assert fired[0].timeout_event == "timeout_art_overdue"

    def test_timeout_rules_exist(self) -> None:
        assert len(TIMEOUT_RULES) >= 3

    def test_all_transitions_have_triggers(self) -> None:
        for t in TRANSITIONS:
            assert t.trigger, f"Transition {t.from_state}->{t.to_state} has no trigger"
