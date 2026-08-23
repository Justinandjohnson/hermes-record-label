"""Coordination rules — maps events to agent actions and state transitions.

Each rule is a pure function: ``(event, machine, bandcamp_bridge) -> list[ActionResult]``.
Rules may call ``machine.transition(...)`` as a side-effect.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from coordination.bandcamp_bridge import BandcampBridge
    from coordination.engine import HermesEvent
    from coordination.state_machine import ReleaseStateMachine

from coordination.state_machine import ReleaseState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Action model
# ---------------------------------------------------------------------------

class ActionResult(BaseModel):
    """An action descriptor produced as a consequence of an event."""

    action_type: str = Field(description="e.g. send_message, trigger_analysis, queue_upload")
    agent: str = Field(description="Target agent that should execute this action")
    payload: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


# ---------------------------------------------------------------------------
# Individual rule handlers
# ---------------------------------------------------------------------------

async def _rule_new_track_detected(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """new_track_detected -> A&R sends 'listening now', trigger audio analysis, DRAFT->IN_REVIEW."""
    actions: list[ActionResult] = []

    # A&R acknowledges receipt.
    actions.append(
        ActionResult(
            action_type="send_message",
            agent="a_and_r",
            payload={
                "track_id": event.track_id,
                "template": "new_track_ack",
                "channel": "sms",
            },
            description="A&R sends 'listening now' acknowledgment",
        )
    )

    # Trigger Gemini audio analysis.
    actions.append(
        ActionResult(
            action_type="trigger_analysis",
            agent="a_and_r",
            payload={
                "track_id": event.track_id,
                "file_path": event.payload.get("file_path"),
            },
            description="Trigger Gemini 3.1 Pro audio analysis",
        )
    )

    # State transition: DRAFT -> IN_REVIEW
    machine.transition(
        trigger="new_track_detected",
        changed_by="a_and_r",
        reason="New track detected — entering review",
    )

    return actions


async def _rule_audio_analysis_complete(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """audio_analysis_complete -> A&R generates+sends feedback, IN_REVIEW->FEEDBACK_GIVEN."""
    actions: list[ActionResult] = []

    actions.append(
        ActionResult(
            action_type="generate_feedback",
            agent="a_and_r",
            payload={
                "track_id": event.track_id,
                "analysis_id": event.payload.get("analysis_id"),
            },
            description="A&R generates in-character feedback from Gemini analysis",
        )
    )

    actions.append(
        ActionResult(
            action_type="send_message",
            agent="a_and_r",
            payload={
                "track_id": event.track_id,
                "template": "analysis_feedback",
                "channel": "sms",
            },
            description="A&R sends creative feedback to artist",
        )
    )

    machine.transition(
        trigger="audio_analysis_complete",
        changed_by="a_and_r",
        reason="Audio analysis complete — feedback generated",
    )

    return actions


async def _rule_artist_approves(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """artist_approves -> Notify Manager+Creative Director, FEEDBACK_GIVEN->APPROVED->ART_NEEDED."""
    actions: list[ActionResult] = []

    # Transition FEEDBACK_GIVEN -> APPROVED.
    machine.transition(
        trigger="artist_approves",
        changed_by=event.agent or "artist",
        reason="Artist approved track",
    )

    # Chain transition: APPROVED -> ART_NEEDED.
    machine.transition(
        trigger="artist_approves",
        changed_by="system",
        reason="Automatic: approved track needs artwork",
    )

    # Notify Manager.
    actions.append(
        ActionResult(
            action_type="send_message",
            agent="manager",
            payload={
                "track_id": event.track_id,
                "template": "track_approved_notification",
            },
            description="Manager notified of track approval",
        )
    )

    # Notify Creative Director.
    actions.append(
        ActionResult(
            action_type="send_message",
            agent="creative_director",
            payload={
                "track_id": event.track_id,
                "template": "artwork_needed",
            },
            description="Creative Director notified — artwork needed",
        )
    )

    return actions


async def _rule_artwork_submitted(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """artwork_submitted -> Creative Director reviews via Gemini Vision."""
    actions: list[ActionResult] = []

    machine.transition(
        trigger="artwork_submitted",
        changed_by=event.agent or "artist",
        reason="Artwork submitted for review",
    )

    actions.append(
        ActionResult(
            action_type="review_artwork",
            agent="creative_director",
            payload={
                "track_id": event.track_id,
                "artwork_path": event.payload.get("artwork_path"),
            },
            description="Creative Director reviews artwork via Gemini Vision",
        )
    )

    return actions


async def _rule_art_approved(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """art_approved -> Notify Manager, ART_SUBMITTED->ART_APPROVED."""
    actions: list[ActionResult] = []

    machine.transition(
        trigger="art_approved",
        changed_by=event.agent or "creative_director",
        reason="Creative Director approved artwork",
    )

    actions.append(
        ActionResult(
            action_type="send_message",
            agent="manager",
            payload={
                "track_id": event.track_id,
                "template": "art_approved_notification",
            },
            description="Manager notified — artwork approved, ready for release date",
        )
    )

    return actions


async def _rule_art_rejected(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """art_rejected -> Creative Director sends notes, ART_SUBMITTED->ART_NEEDED."""
    actions: list[ActionResult] = []

    machine.transition(
        trigger="art_rejected",
        changed_by=event.agent or "creative_director",
        reason=event.payload.get("reason", "Artwork rejected"),
    )

    actions.append(
        ActionResult(
            action_type="send_message",
            agent="creative_director",
            payload={
                "track_id": event.track_id,
                "template": "art_rejection_notes",
                "notes": event.payload.get("notes", ""),
                "channel": "sms",
            },
            description="Creative Director sends rejection notes to artist",
        )
    )

    return actions


async def _rule_release_date_set(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """release_date_set -> Bandcamp preflight, ART_APPROVED->RELEASE_READY->PREFLIGHT."""
    actions: list[ActionResult] = []

    # ART_APPROVED -> RELEASE_READY
    machine.transition(
        trigger="release_date_set",
        changed_by=event.agent or "manager",
        reason=f"Release date set: {event.payload.get('release_date')}",
    )

    # RELEASE_READY -> PREFLIGHT
    machine.transition(
        trigger="release_date_set",
        changed_by="system",
        reason="Triggering Bandcamp preflight check",
    )

    # Trigger preflight via Bandcamp bridge.
    album_paths = event.payload.get("album_paths", [])
    if bandcamp_bridge and album_paths:
        actions.append(
            ActionResult(
                action_type="bandcamp_preflight",
                agent="bandcamp_agent",
                payload={"album_paths": album_paths},
                description="Run Bandcamp preflight check",
            )
        )

    return actions


async def _rule_preflight_passed(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """preflight_passed -> Approve+queue upload, PREFLIGHT->UPLOADING."""
    actions: list[ActionResult] = []

    machine.transition(
        trigger="preflight_passed",
        changed_by="bandcamp_agent",
        reason="Preflight passed — queuing upload",
    )

    album_paths = event.payload.get("album_paths", [])
    if bandcamp_bridge and album_paths:
        actions.append(
            ActionResult(
                action_type="bandcamp_approve",
                agent="bandcamp_agent",
                payload={"album_paths": album_paths, "reviewer": "a_and_r"},
                description="Approve album for publish on Bandcamp",
            )
        )
        actions.append(
            ActionResult(
                action_type="bandcamp_upload",
                agent="bandcamp_agent",
                payload={"album_paths": album_paths, "publish": True},
                description="Queue Bandcamp upload job",
            )
        )

    return actions


async def _rule_preflight_failed(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """preflight_failed -> Route to correct agent based on failure reason."""
    actions: list[ActionResult] = []
    failure_reason = event.payload.get("failure_reason", "unknown")

    if failure_reason == "missing_cover":
        machine.transition(
            trigger="preflight_failed",
            changed_by="bandcamp_agent",
            reason="Preflight failed: missing cover art",
            guard="missing_cover",
        )
        actions.append(
            ActionResult(
                action_type="send_message",
                agent="creative_director",
                payload={
                    "track_id": event.track_id,
                    "template": "preflight_missing_cover",
                },
                description="Creative Director notified: cover art missing/invalid",
            )
        )
    else:
        # Default to metadata issues.
        machine.transition(
            trigger="preflight_failed",
            changed_by="bandcamp_agent",
            reason=f"Preflight failed: {failure_reason}",
            guard="bad_metadata",
        )
        actions.append(
            ActionResult(
                action_type="send_message",
                agent="a_and_r",
                payload={
                    "track_id": event.track_id,
                    "template": "preflight_metadata_issue",
                    "details": event.payload.get("details", ""),
                },
                description="A&R notified of metadata issues",
            )
        )

    return actions


async def _rule_upload_completed(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """upload_completed -> Celebrate, update stats, UPLOADING->RELEASED."""
    actions: list[ActionResult] = []

    machine.transition(
        trigger="upload_completed",
        changed_by="bandcamp_agent",
        reason="Upload confirmed — track is live on Bandcamp",
    )

    # Celebration message from Manager.
    actions.append(
        ActionResult(
            action_type="send_message",
            agent="manager",
            payload={
                "track_id": event.track_id,
                "template": "release_celebration",
                "channel": "sms",
            },
            description="Manager sends celebration message to artist",
        )
    )

    # Update gamification stats.
    actions.append(
        ActionResult(
            action_type="update_stats",
            agent="system",
            payload={
                "track_id": event.track_id,
                "event": "track_released",
                "bandcamp_url": event.payload.get("bandcamp_url"),
            },
            description="Update artist stats and gamification data",
        )
    )

    return actions


async def _rule_upload_failed(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """upload_failed -> Manager notified, retry from RELEASE_READY."""
    actions: list[ActionResult] = []

    machine.transition(
        trigger="upload_failed",
        changed_by="bandcamp_agent",
        reason=f"Upload failed: {event.payload.get('error', 'unknown')}",
    )

    actions.append(
        ActionResult(
            action_type="send_message",
            agent="manager",
            payload={
                "track_id": event.track_id,
                "template": "upload_failed_notification",
                "error": event.payload.get("error", ""),
            },
            description="Manager notified of upload failure — will retry",
        )
    )

    return actions


async def _rule_artist_message_inbound(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """artist_message_inbound -> Route to A&R as primary responder."""
    return [
        ActionResult(
            action_type="route_message",
            agent="a_and_r",
            payload={
                "track_id": event.track_id,
                "message": event.payload.get("message", ""),
                "channel": event.payload.get("channel", "sms"),
            },
            description="Route artist message to A&R for response",
        ),
    ]


async def _rule_revision_uploaded(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """revision_uploaded -> FEEDBACK_GIVEN->DRAFT, A&R re-reviews the track."""
    actions: list[ActionResult] = []

    machine.transition(
        trigger="revision_uploaded",
        changed_by=event.agent or "artist",
        reason="Artist uploaded a revision — restarting review",
    )

    actions.append(
        ActionResult(
            action_type="send_message",
            agent="a_and_r",
            payload={
                "track_id": event.track_id,
                "template": "revision_ack",
                "channel": "sms",
            },
            description="A&R acknowledges revision upload",
        )
    )

    actions.append(
        ActionResult(
            action_type="trigger_analysis",
            agent="a_and_r",
            payload={
                "track_id": event.track_id,
                "file_path": event.payload.get("file_path"),
            },
            description="Trigger re-analysis on revised track",
        )
    )

    return actions


async def _rule_artist_requests_redo(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """artist_requests_redo -> Any state -> DRAFT."""
    actions: list[ActionResult] = []

    machine.transition(
        trigger="artist_requests_redo",
        changed_by=event.agent or "artist",
        reason="Artist requested redo",
    )

    actions.append(
        ActionResult(
            action_type="send_message",
            agent="a_and_r",
            payload={
                "track_id": event.track_id,
                "template": "redo_acknowledged",
                "channel": "sms",
            },
            description="A&R acknowledges redo request",
        )
    )

    return actions


async def _rule_timeout_feedback_stale(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """timeout_feedback_stale -> Manager nags artist."""
    return [
        ActionResult(
            action_type="send_nag",
            agent="manager",
            payload={
                "track_id": event.track_id,
                "template": "feedback_stale_nag",
                "channel": "sms",
            },
            description="Manager nags artist about unanswered feedback",
        )
    ]


async def _rule_timeout_art_overdue(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """timeout_art_overdue -> Creative Director escalates."""
    return [
        ActionResult(
            action_type="send_nag",
            agent="creative_director",
            payload={
                "track_id": event.track_id,
                "template": "art_overdue_escalation",
                "channel": "sms",
            },
            description="Creative Director escalates overdue artwork",
        )
    ]


async def _rule_timeout_release_date_missed(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """timeout_release_date_missed -> Manager suggests new date."""
    return [
        ActionResult(
            action_type="send_message",
            agent="manager",
            payload={
                "track_id": event.track_id,
                "template": "missed_release_date",
                "channel": "sms",
            },
            description="Manager suggests new release date",
        )
    ]


# ---------------------------------------------------------------------------
# Intake lifecycle
# ---------------------------------------------------------------------------


async def _rule_intake_complete(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """intake_complete -> Manager notified, A&R knows file is processed and ready."""
    return [
        ActionResult(
            action_type="send_message",
            agent="manager",
            payload={
                "track_id": event.track_id,
                "template": "intake_complete_notification",
            },
            description="Manager notified — new track processed and ready for review",
        ),
    ]


# ---------------------------------------------------------------------------
# Track approval / rejection (A&R decisions)
# ---------------------------------------------------------------------------


async def _rule_track_approved(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """track_approved -> Exec panel fires (Janick, Rhone, Rubin delayed), Manager + CD notified."""
    actions: list[ActionResult] = []

    machine.transition(
        trigger="track_approved",
        changed_by=event.agent or "a_and_r",
        reason="A&R approved track",
    )

    # Chain: APPROVED -> ART_NEEDED
    machine.transition(
        trigger="track_approved",
        changed_by="system",
        reason="Automatic: approved track needs artwork",
    )

    # Notify Manager
    actions.append(
        ActionResult(
            action_type="send_message",
            agent="manager",
            payload={"track_id": event.track_id, "template": "track_approved_notification"},
            description="Manager notified of track approval",
        )
    )

    # Notify Creative Director — artwork phase begins
    actions.append(
        ActionResult(
            action_type="send_message",
            agent="creative_director",
            payload={"track_id": event.track_id, "template": "artwork_needed"},
            description="Creative Director notified — artwork needed",
        )
    )

    # Exec panel fires with staggered delays (handled by agent timing config):
    # Janick (2h delay), Rhone (3h delay), Rubin (5h delay)
    for exec_agent in ("janick", "rhone", "rubin"):
        actions.append(
            ActionResult(
                action_type="trigger_agent",
                agent=exec_agent,
                payload={"track_id": event.track_id, "trigger_event": "track_approved"},
                description=f"{exec_agent} exec panel review triggered",
            )
        )

    return actions


async def _rule_track_rejected(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """track_rejected -> State back to FEEDBACK_GIVEN, rejection logged."""
    machine.transition(
        trigger="track_rejected",
        changed_by=event.agent or "a_and_r",
        reason=event.payload.get("reason", "A&R rejected track"),
    )
    return []


# ---------------------------------------------------------------------------
# Stem separation
# ---------------------------------------------------------------------------


async def _rule_stems_ready(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """stems_ready -> A&R can now access lyrics; Rubin can do essence analysis."""
    actions: list[ActionResult] = []

    actions.append(
        ActionResult(
            action_type="notify",
            agent="a_and_r",
            payload={"track_id": event.track_id, "template": "stems_ready_notification"},
            description="A&R notified — stems separated, lyrics available",
        )
    )

    actions.append(
        ActionResult(
            action_type="notify",
            agent="rubin",
            payload={"track_id": event.track_id, "template": "stems_ready_for_essence"},
            description="Rubin notified — stems ready for essence analysis",
        )
    )

    return actions


# ---------------------------------------------------------------------------
# Exec panel events
# ---------------------------------------------------------------------------


async def _rule_early_conviction_signal(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """early_conviction_signal -> Kallman has gut conviction. Log it, notify A&R."""
    return [
        ActionResult(
            action_type="log_panel_signal",
            agent="kallman",
            payload={
                "track_id": event.track_id,
                "signal_type": "early_conviction",
                "positive": True,
                "message": event.payload.get("message", ""),
            },
            description="Kallman early conviction logged",
        ),
        ActionResult(
            action_type="notify",
            agent="a_and_r",
            payload={
                "track_id": event.track_id,
                "from_agent": "kallman",
                "signal": "early_conviction",
                "message": event.payload.get("message", ""),
            },
            description="A&R sees Kallman's conviction signal",
        ),
    ]


async def _rule_early_conviction_pass(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """early_conviction_pass -> Kallman didn't feel strong conviction. Logged, no alert."""
    return [
        ActionResult(
            action_type="log_panel_signal",
            agent="kallman",
            payload={
                "track_id": event.track_id,
                "signal_type": "early_conviction",
                "positive": False,
            },
            description="Kallman pass logged — no strong gut reaction",
        ),
    ]


async def _rule_vision_confirmed(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """vision_confirmed -> Janick sees a coherent world. Positive signal logged."""
    return [
        ActionResult(
            action_type="log_panel_signal",
            agent="janick",
            payload={
                "track_id": event.track_id,
                "signal_type": "vision_coherence",
                "positive": True,
                "message": event.payload.get("message", ""),
            },
            description="Janick vision confirmation logged",
        ),
    ]


async def _rule_vision_gap_flagged(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """vision_gap_flagged -> Janick sees a coherence gap. Alert A&R."""
    return [
        ActionResult(
            action_type="log_panel_signal",
            agent="janick",
            payload={
                "track_id": event.track_id,
                "signal_type": "vision_coherence",
                "positive": False,
                "message": event.payload.get("message", ""),
            },
            description="Janick vision gap flagged",
        ),
        ActionResult(
            action_type="notify",
            agent="a_and_r",
            payload={
                "track_id": event.track_id,
                "from_agent": "janick",
                "signal": "vision_gap",
                "message": event.payload.get("message", ""),
            },
            description="A&R alerted to Janick's vision gap concern",
        ),
    ]


async def _rule_cultural_auth_confirmed(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """cultural_auth_confirmed -> Rhone confirmed authenticity. Positive signal logged."""
    return [
        ActionResult(
            action_type="log_panel_signal",
            agent="rhone",
            payload={
                "track_id": event.track_id,
                "signal_type": "cultural_authenticity",
                "positive": True,
                "message": event.payload.get("message", ""),
            },
            description="Rhone cultural authenticity confirmed",
        ),
    ]


async def _rule_cultural_auth_flagged(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """cultural_auth_flagged -> Rhone has an authenticity concern. Alert A&R."""
    return [
        ActionResult(
            action_type="log_panel_signal",
            agent="rhone",
            payload={
                "track_id": event.track_id,
                "signal_type": "cultural_authenticity",
                "positive": False,
                "message": event.payload.get("message", ""),
            },
            description="Rhone cultural authenticity concern flagged",
        ),
        ActionResult(
            action_type="notify",
            agent="a_and_r",
            payload={
                "track_id": event.track_id,
                "from_agent": "rhone",
                "signal": "cultural_concern",
                "message": event.payload.get("message", ""),
            },
            description="A&R alerted to Rhone's cultural concern",
        ),
    ]


async def _rule_production_truth_noted(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """production_truth_noted -> Rubin dropped a truth. Log it, A&R sees it."""
    return [
        ActionResult(
            action_type="log_panel_signal",
            agent="rubin",
            payload={
                "track_id": event.track_id,
                "signal_type": "production_truth",
                "positive": True,
                "message": event.payload.get("message", ""),
            },
            description="Rubin production truth logged",
        ),
        ActionResult(
            action_type="notify",
            agent="a_and_r",
            payload={
                "track_id": event.track_id,
                "from_agent": "rubin",
                "signal": "production_truth",
                "message": event.payload.get("message", ""),
            },
            description="A&R sees Rubin's production observation",
        ),
    ]


# ---------------------------------------------------------------------------
# Bandcamp release pipeline
# ---------------------------------------------------------------------------


async def _rule_release_ready(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """release_ready -> All gates cleared. Bandcamp runs preflight check."""
    actions: list[ActionResult] = []

    machine.transition(
        trigger="release_ready",
        changed_by="system",
        reason="All gates cleared — triggering Bandcamp preflight",
    )

    actions.append(
        ActionResult(
            action_type="bandcamp_preflight",
            agent="bandcamp",
            payload={
                "track_id": event.track_id,
                "project_id": event.payload.get("project_id"),
            },
            description="Bandcamp runs preflight check on release package",
        )
    )

    return actions


async def _rule_preflight_requested(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """preflight_requested -> Manual preflight triggered. Bandcamp validates release package."""
    return [
        ActionResult(
            action_type="bandcamp_preflight",
            agent="bandcamp",
            payload={
                "track_id": event.track_id,
                "project_id": event.payload.get("project_id"),
                "manual": True,
            },
            description="Bandcamp validates release package (manual trigger)",
        )
    ]


async def _rule_publish_approved(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """publish_approved -> Artist/Manager approved publish. Bandcamp goes live.

    No state machine transition here — publish approval is a Bandcamp directive
    (draft → live), not a release-pipeline state change. The pipeline state
    already moved through preflight_passed → UPLOADING → RELEASED separately.
    """
    actions: list[ActionResult] = []

    actions.append(
        ActionResult(
            action_type="bandcamp_publish",
            agent="bandcamp",
            payload={
                "track_id": event.track_id,
                "project_id": event.payload.get("project_id"),
                "release_date": event.payload.get("release_date"),
            },
            description="Bandcamp publishes release live",
        )
    )

    # Manager gets confirmation that publish was initiated
    actions.append(
        ActionResult(
            action_type="notify",
            agent="manager",
            payload={
                "track_id": event.track_id,
                "template": "publish_initiated",
            },
            description="Manager notified publish was initiated",
        )
    )

    return actions


async def _rule_preflight_art_failed(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """preflight_art_failed -> Bandcamp preflight failed due to artwork. Creative Director notified."""
    actions: list[ActionResult] = []

    actions.append(
        ActionResult(
            action_type="send_message",
            agent="creative_director",
            payload={
                "track_id": event.track_id,
                "template": "preflight_art_failed",
                "details": event.payload.get("details", ""),
                "channel": "sms",
            },
            description="Creative Director notified — artwork failed Bandcamp preflight",
        )
    )

    # Also log on bandcamp side for retry tracking
    actions.append(
        ActionResult(
            action_type="notify",
            agent="bandcamp",
            payload={
                "track_id": event.track_id,
                "status": "awaiting_art_fix",
                "details": event.payload.get("details", ""),
            },
            description="Bandcamp flagged as awaiting artwork fix",
        )
    )

    return actions


# ---------------------------------------------------------------------------
# Scheduling / cadence events
# ---------------------------------------------------------------------------


async def _rule_weekly_summary_due(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """weekly_summary_due -> Manager compiles and sends weekly activity summary."""
    return [
        ActionResult(
            action_type="send_weekly_summary",
            agent="manager",
            payload={
                "template": "weekly_summary",
                "channel": "sms",
                "week_of": event.payload.get("week_of", ""),
            },
            description="Manager sends weekly activity summary to artist",
        )
    ]


async def _rule_timeout_art_3d(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """timeout_art_3d -> Artwork not submitted with 3 days to release. Escalate."""
    actions: list[ActionResult] = []

    # Creative Director sends urgent reminder to artist
    actions.append(
        ActionResult(
            action_type="send_nag",
            agent="creative_director",
            payload={
                "track_id": event.track_id,
                "template": "art_3d_urgency",
                "release_date": event.payload.get("release_date", ""),
                "channel": "sms",
            },
            description="Creative Director sends 3-day artwork urgency notice",
        )
    )

    # Manager also alerted so they can apply pressure if needed
    actions.append(
        ActionResult(
            action_type="notify",
            agent="manager",
            payload={
                "track_id": event.track_id,
                "template": "art_3d_manager_alert",
                "release_date": event.payload.get("release_date", ""),
            },
            description="Manager alerted — artwork still missing with 3 days to release",
        )
    )

    return actions


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def _rule_import_requested(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """import_requested -> Manual import triggered. Intake processes the folder."""
    return [
        ActionResult(
            action_type="trigger_intake",
            agent="intake",
            payload={
                "folder_path": event.payload.get("folder_path", ""),
                "project_id": event.payload.get("project_id"),
                "manual": True,
            },
            description="Intake processes manually imported folder",
        )
    ]


async def _rule_approval_revoked(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None,
) -> list[ActionResult]:
    """approval_revoked -> Force state back to FEEDBACK_GIVEN, notify Manager.

    Uses force_state because revocation can happen from any post-approval state
    (APPROVED, ART_NEEDED, ART_SUBMITTED, ART_APPROVED, RELEASE_READY).
    """
    actions: list[ActionResult] = []

    machine.force_state(
        state=ReleaseState.FEEDBACK_GIVEN,
        changed_by=event.agent or "a_and_r",
        reason=event.payload.get("reason", "Approval revoked — track returned to review"),
    )

    actions.append(
        ActionResult(
            action_type="notify",
            agent="manager",
            payload={
                "track_id": event.track_id,
                "template": "approval_revoked_notification",
                "reason": event.payload.get("reason", ""),
            },
            description="Manager notified — A&R revoked approval",
        )
    )

    return actions


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------

_RULE_HANDLERS: dict[str, Any] = {
    # Track lifecycle
    "new_track_detected": _rule_new_track_detected,
    "audio_analysis_complete": _rule_audio_analysis_complete,
    "intake_complete": _rule_intake_complete,
    "track_approved": _rule_track_approved,
    "track_rejected": _rule_track_rejected,
    "artist_approves": _rule_artist_approves,
    "artist_message_inbound": _rule_artist_message_inbound,
    "revision_uploaded": _rule_revision_uploaded,
    "artist_requests_redo": _rule_artist_requests_redo,
    # Stems
    "stems_ready": _rule_stems_ready,
    # Exec panel
    "early_conviction_signal": _rule_early_conviction_signal,
    "early_conviction_pass": _rule_early_conviction_pass,
    "vision_confirmed": _rule_vision_confirmed,
    "vision_gap_flagged": _rule_vision_gap_flagged,
    "cultural_auth_confirmed": _rule_cultural_auth_confirmed,
    "cultural_auth_flagged": _rule_cultural_auth_flagged,
    "production_truth_noted": _rule_production_truth_noted,
    # Artwork
    "artwork_submitted": _rule_artwork_submitted,
    "art_approved": _rule_art_approved,
    "art_rejected": _rule_art_rejected,
    # Release pipeline
    "release_date_set": _rule_release_date_set,
    "preflight_passed": _rule_preflight_passed,
    "preflight_failed": _rule_preflight_failed,
    "upload_completed": _rule_upload_completed,
    "upload_failed": _rule_upload_failed,
    # Bandcamp release pipeline
    "release_ready": _rule_release_ready,
    "preflight_requested": _rule_preflight_requested,
    "publish_approved": _rule_publish_approved,
    "preflight_art_failed": _rule_preflight_art_failed,
    # Scheduling / cadence
    "weekly_summary_due": _rule_weekly_summary_due,
    "timeout_art_3d": _rule_timeout_art_3d,
    # Edge cases
    "import_requested": _rule_import_requested,
    "approval_revoked": _rule_approval_revoked,
    # Timeout events
    "timeout_feedback_stale": _rule_timeout_feedback_stale,
    "timeout_art_overdue": _rule_timeout_art_overdue,
    "timeout_release_date_missed": _rule_timeout_release_date_missed,
}


async def apply_rules(
    event: HermesEvent,
    machine: ReleaseStateMachine,
    bandcamp_bridge: BandcampBridge | None = None,
) -> list[ActionResult]:
    """Look up and execute the rule handler for the given event.

    Returns the list of ``ActionResult`` objects to be dispatched.
    Raises ``KeyError`` if no handler is registered for the event type.
    """
    handler = _RULE_HANDLERS.get(event.event_type)
    if handler is None:
        logger.warning("No rule handler registered for event type '%s'", event.event_type)
        return []

    return await handler(event, machine, bandcamp_bridge)
