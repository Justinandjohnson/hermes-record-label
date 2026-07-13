"""Tests for coordination rules — event-to-action mapping."""

from __future__ import annotations

import pytest

from coordination.engine import HermesEvent
from coordination.rules import apply_rules
from coordination.state_machine import ReleaseState, ReleaseStateMachine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(event_type: str, track_id: int = 1, **payload: object) -> HermesEvent:
    return HermesEvent(event_type=event_type, track_id=track_id, payload=payload)


def _make_machine(state: ReleaseState, track_id: int = 1) -> ReleaseStateMachine:
    return ReleaseStateMachine(track_id=track_id, initial_state=state)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNewTrackDetected:
    @pytest.mark.asyncio
    async def test_produces_ack_and_analysis(self) -> None:
        machine = _make_machine(ReleaseState.DRAFT)
        event = _make_event("new_track_detected", file_path="/music/track.wav")

        actions = await apply_rules(event, machine)

        assert machine.state == ReleaseState.IN_REVIEW
        assert len(actions) == 2
        action_types = {a.action_type for a in actions}
        assert "send_message" in action_types
        assert "trigger_analysis" in action_types

    @pytest.mark.asyncio
    async def test_ack_targets_a_and_r(self) -> None:
        machine = _make_machine(ReleaseState.DRAFT)
        event = _make_event("new_track_detected")
        actions = await apply_rules(event, machine)
        assert all(a.agent == "a_and_r" for a in actions)


class TestAudioAnalysisComplete:
    @pytest.mark.asyncio
    async def test_generates_feedback(self) -> None:
        machine = _make_machine(ReleaseState.IN_REVIEW)
        event = _make_event("audio_analysis_complete", analysis_id=42)

        actions = await apply_rules(event, machine)

        assert machine.state == ReleaseState.FEEDBACK_GIVEN
        assert len(actions) == 2
        assert any(a.action_type == "generate_feedback" for a in actions)
        assert any(a.action_type == "send_message" for a in actions)


class TestArtistApproves:
    @pytest.mark.asyncio
    async def test_chain_transition_to_art_needed(self) -> None:
        machine = _make_machine(ReleaseState.FEEDBACK_GIVEN)
        event = _make_event("artist_approves")

        actions = await apply_rules(event, machine)

        # Should chain: FEEDBACK_GIVEN -> APPROVED -> ART_NEEDED
        assert machine.state == ReleaseState.ART_NEEDED
        # Should notify manager and creative_director.
        agents_notified = {a.agent for a in actions}
        assert "manager" in agents_notified
        assert "creative_director" in agents_notified


class TestArtworkSubmitted:
    @pytest.mark.asyncio
    async def test_triggers_review(self) -> None:
        machine = _make_machine(ReleaseState.ART_NEEDED)
        event = _make_event("artwork_submitted", artwork_path="/art/cover.png")

        actions = await apply_rules(event, machine)

        assert machine.state == ReleaseState.ART_SUBMITTED
        assert any(a.action_type == "review_artwork" for a in actions)


class TestArtApproved:
    @pytest.mark.asyncio
    async def test_notifies_manager(self) -> None:
        machine = _make_machine(ReleaseState.ART_SUBMITTED)
        event = _make_event("art_approved")
        actions = await apply_rules(event, machine)

        assert machine.state == ReleaseState.ART_APPROVED
        assert any(a.agent == "manager" for a in actions)


class TestArtRejected:
    @pytest.mark.asyncio
    async def test_sends_notes(self) -> None:
        machine = _make_machine(ReleaseState.ART_SUBMITTED)
        event = _make_event("art_rejected", notes="Too dark, needs more contrast")

        actions = await apply_rules(event, machine)

        assert machine.state == ReleaseState.ART_NEEDED
        assert any(a.action_type == "send_message" and a.agent == "creative_director" for a in actions)


class TestReleaseDateSet:
    @pytest.mark.asyncio
    async def test_triggers_preflight(self) -> None:
        machine = _make_machine(ReleaseState.ART_APPROVED)
        event = _make_event("release_date_set", release_date="2026-06-01", album_paths=["/music/album"])

        actions = await apply_rules(event, machine)

        # Should chain: ART_APPROVED -> RELEASE_READY -> PREFLIGHT
        assert machine.state == ReleaseState.PREFLIGHT


class TestPreflightPassed:
    @pytest.mark.asyncio
    async def test_queues_upload(self) -> None:
        machine = _make_machine(ReleaseState.PREFLIGHT)
        event = _make_event("preflight_passed", album_paths=["/music/album"])

        actions = await apply_rules(event, machine)

        assert machine.state == ReleaseState.UPLOADING


class TestPreflightFailed:
    @pytest.mark.asyncio
    async def test_missing_cover_routes_to_creative_director(self) -> None:
        machine = _make_machine(ReleaseState.PREFLIGHT)
        event = _make_event("preflight_failed", failure_reason="missing_cover")

        actions = await apply_rules(event, machine)

        assert machine.state == ReleaseState.ART_NEEDED
        assert any(a.agent == "creative_director" for a in actions)

    @pytest.mark.asyncio
    async def test_bad_metadata_routes_to_a_and_r(self) -> None:
        machine = _make_machine(ReleaseState.PREFLIGHT)
        event = _make_event("preflight_failed", failure_reason="bad_metadata")

        actions = await apply_rules(event, machine)

        assert machine.state == ReleaseState.FEEDBACK_GIVEN
        assert any(a.agent == "a_and_r" for a in actions)


class TestUploadCompleted:
    @pytest.mark.asyncio
    async def test_celebrates_and_updates_stats(self) -> None:
        machine = _make_machine(ReleaseState.UPLOADING)
        event = _make_event("upload_completed", bandcamp_url="https://artist.bandcamp.com/track/new")

        actions = await apply_rules(event, machine)

        assert machine.state == ReleaseState.RELEASED
        action_types = {a.action_type for a in actions}
        assert "send_message" in action_types
        assert "update_stats" in action_types


class TestUploadFailed:
    @pytest.mark.asyncio
    async def test_notifies_manager(self) -> None:
        machine = _make_machine(ReleaseState.UPLOADING)
        event = _make_event("upload_failed", error="Connection timeout")

        actions = await apply_rules(event, machine)

        assert machine.state == ReleaseState.RELEASE_READY
        assert any(a.agent == "manager" for a in actions)


class TestArtistRequestsRedo:
    @pytest.mark.asyncio
    async def test_resets_to_draft(self) -> None:
        machine = _make_machine(ReleaseState.ART_APPROVED)
        event = _make_event("artist_requests_redo")

        actions = await apply_rules(event, machine)

        assert machine.state == ReleaseState.DRAFT
        assert any(a.agent == "a_and_r" for a in actions)


class TestUnknownEvent:
    @pytest.mark.asyncio
    async def test_returns_empty_actions(self) -> None:
        machine = _make_machine(ReleaseState.DRAFT)
        event = _make_event("totally_unknown_event")

        actions = await apply_rules(event, machine)
        assert actions == []


class TestTimeoutEvents:
    @pytest.mark.asyncio
    async def test_feedback_stale_nag(self) -> None:
        machine = _make_machine(ReleaseState.FEEDBACK_GIVEN)
        event = _make_event("timeout_feedback_stale")
        actions = await apply_rules(event, machine)
        assert any(a.action_type == "send_nag" and a.agent == "manager" for a in actions)

    @pytest.mark.asyncio
    async def test_art_overdue_escalation(self) -> None:
        machine = _make_machine(ReleaseState.ART_NEEDED)
        event = _make_event("timeout_art_overdue")
        actions = await apply_rules(event, machine)
        assert any(a.agent == "creative_director" for a in actions)

    @pytest.mark.asyncio
    async def test_release_date_missed(self) -> None:
        machine = _make_machine(ReleaseState.RELEASE_READY)
        event = _make_event("timeout_release_date_missed")
        actions = await apply_rules(event, machine)
        assert any(a.agent == "manager" for a in actions)
