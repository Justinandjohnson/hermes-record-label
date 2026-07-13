"""Release state machine — states, transitions, guards, and timeout rules.

Each release (track or album) moves through a well-defined pipeline from DRAFT
to RELEASED.  Transitions are guarded and every change is logged atomically to
the ``release_states`` table.
"""

from __future__ import annotations

import enum
import logging
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

class ReleaseState(str, enum.Enum):
    """Every possible state a release can occupy."""

    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    FEEDBACK_GIVEN = "FEEDBACK_GIVEN"
    APPROVED = "APPROVED"
    ART_NEEDED = "ART_NEEDED"
    ART_SUBMITTED = "ART_SUBMITTED"
    ART_APPROVED = "ART_APPROVED"
    RELEASE_READY = "RELEASE_READY"
    PREFLIGHT = "PREFLIGHT"
    UPLOADING = "UPLOADING"
    RELEASED = "RELEASED"


# ---------------------------------------------------------------------------
# Transition definition
# ---------------------------------------------------------------------------

class Transition(BaseModel):
    """A single allowed state transition with optional guard metadata."""

    from_state: ReleaseState
    to_state: ReleaseState
    trigger: str = Field(description="Event name that causes this transition")
    guard: str | None = Field(
        default=None,
        description="Named guard condition that must be truthy for the transition to fire",
    )
    description: str = ""


# ---------------------------------------------------------------------------
# Transition log entry (mirrors release_states table row)
# ---------------------------------------------------------------------------

class StateTransitionLog(BaseModel):
    """Persisted record of a state change."""

    track_id: int
    from_state: ReleaseState | None
    to_state: ReleaseState
    changed_by: str = Field(description="Agent or system that triggered the change")
    reason: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Timeout rules
# ---------------------------------------------------------------------------

class TimeoutRule(BaseModel):
    """When a release sits in a state too long, generate a timeout event."""

    state: ReleaseState
    max_duration: timedelta
    timeout_event: str
    description: str = ""


TIMEOUT_RULES: list[TimeoutRule] = [
    TimeoutRule(
        state=ReleaseState.FEEDBACK_GIVEN,
        max_duration=timedelta(days=7),
        timeout_event="timeout_feedback_stale",
        description="Manager nags artist when feedback sits unanswered for >7 days",
    ),
    TimeoutRule(
        state=ReleaseState.ART_NEEDED,
        max_duration=timedelta(days=3),
        timeout_event="timeout_art_overdue",
        description="Creative Director escalates when artwork is late relative to release date",
    ),
    TimeoutRule(
        state=ReleaseState.RELEASE_READY,
        max_duration=timedelta(days=0),  # checked against release_date, not duration
        timeout_event="timeout_release_date_missed",
        description="Manager suggests new date when release date has passed",
    ),
]


# ---------------------------------------------------------------------------
# Allowed transitions registry
# ---------------------------------------------------------------------------

TRANSITIONS: list[Transition] = [
    # --- Early pipeline ---
    Transition(
        from_state=ReleaseState.DRAFT,
        to_state=ReleaseState.IN_REVIEW,
        trigger="new_track_detected",
        description="Automatic on file detection",
    ),
    Transition(
        from_state=ReleaseState.IN_REVIEW,
        to_state=ReleaseState.FEEDBACK_GIVEN,
        trigger="audio_analysis_complete",
        description="Automatic after audio analysis",
    ),
    Transition(
        from_state=ReleaseState.FEEDBACK_GIVEN,
        to_state=ReleaseState.DRAFT,
        trigger="revision_uploaded",
        description="Artist uploads revision",
    ),
    Transition(
        from_state=ReleaseState.FEEDBACK_GIVEN,
        to_state=ReleaseState.APPROVED,
        trigger="artist_approves",
        description="Artist or A&R explicitly approves",
    ),
    # A&R approves the track directly (separate from artist self-approval)
    Transition(
        from_state=ReleaseState.FEEDBACK_GIVEN,
        to_state=ReleaseState.APPROVED,
        trigger="track_approved",
        description="A&R approved track after review",
    ),
    Transition(
        from_state=ReleaseState.IN_REVIEW,
        to_state=ReleaseState.APPROVED,
        trigger="track_approved",
        description="A&R approved track during review (fast-track)",
    ),
    # A&R rejection — return to FEEDBACK_GIVEN
    Transition(
        from_state=ReleaseState.APPROVED,
        to_state=ReleaseState.FEEDBACK_GIVEN,
        trigger="track_rejected",
        description="A&R reverted approval — track needs more work",
    ),
    Transition(
        from_state=ReleaseState.IN_REVIEW,
        to_state=ReleaseState.FEEDBACK_GIVEN,
        trigger="track_rejected",
        description="A&R rejected track during review",
    ),
    # --- Artwork pipeline ---
    Transition(
        from_state=ReleaseState.APPROVED,
        to_state=ReleaseState.ART_NEEDED,
        trigger="artist_approves",
        description="Automatic chain: approved -> art needed",
    ),
    Transition(
        from_state=ReleaseState.APPROVED,
        to_state=ReleaseState.ART_NEEDED,
        trigger="track_approved",
        description="Automatic chain: A&R approved -> art needed",
    ),
    Transition(
        from_state=ReleaseState.ART_NEEDED,
        to_state=ReleaseState.ART_SUBMITTED,
        trigger="artwork_submitted",
        description="Artist uploads artwork",
    ),
    Transition(
        from_state=ReleaseState.ART_SUBMITTED,
        to_state=ReleaseState.ART_NEEDED,
        trigger="art_rejected",
        description="Creative Director rejects artwork",
    ),
    Transition(
        from_state=ReleaseState.ART_SUBMITTED,
        to_state=ReleaseState.ART_APPROVED,
        trigger="art_approved",
        description="Creative Director approves artwork",
    ),
    # --- Release pipeline ---
    Transition(
        from_state=ReleaseState.ART_APPROVED,
        to_state=ReleaseState.RELEASE_READY,
        trigger="release_date_set",
        description="Manager sets release date",
    ),
    Transition(
        from_state=ReleaseState.RELEASE_READY,
        to_state=ReleaseState.PREFLIGHT,
        trigger="release_date_set",
        description="Bandcamp preflight triggered",
    ),
    Transition(
        from_state=ReleaseState.RELEASE_READY,
        to_state=ReleaseState.PREFLIGHT,
        trigger="release_ready",
        description="All gates cleared — Bandcamp preflight triggered",
    ),
    Transition(
        from_state=ReleaseState.PREFLIGHT,
        to_state=ReleaseState.UPLOADING,
        trigger="preflight_passed",
        description="Preflight passed, queue upload",
    ),
    Transition(
        from_state=ReleaseState.PREFLIGHT,
        to_state=ReleaseState.ART_NEEDED,
        trigger="preflight_failed",
        guard="missing_cover",
        description="Preflight failed due to missing cover art",
    ),
    Transition(
        from_state=ReleaseState.PREFLIGHT,
        to_state=ReleaseState.FEEDBACK_GIVEN,
        trigger="preflight_failed",
        guard="bad_metadata",
        description="Preflight failed due to metadata issues",
    ),
    Transition(
        from_state=ReleaseState.UPLOADING,
        to_state=ReleaseState.RELEASED,
        trigger="upload_completed",
        description="Upload success",
    ),
    Transition(
        from_state=ReleaseState.UPLOADING,
        to_state=ReleaseState.RELEASE_READY,
        trigger="upload_failed",
        description="Upload failed, retry from release_ready",
    ),
]

# Universal redo: any state -> DRAFT when artist requests it.
_REDO_SOURCES = [s for s in ReleaseState if s != ReleaseState.DRAFT]
for _src in _REDO_SOURCES:
    TRANSITIONS.append(
        Transition(
            from_state=_src,
            to_state=ReleaseState.DRAFT,
            trigger="artist_requests_redo",
            description=f"Artist requests redo from {_src.value}",
        )
    )

# Build a fast lookup: (from_state, trigger) -> list[Transition]
_TRANSITION_INDEX: dict[tuple[ReleaseState, str], list[Transition]] = {}
for _t in TRANSITIONS:
    _TRANSITION_INDEX.setdefault((_t.from_state, _t.trigger), []).append(_t)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class InvalidTransition(Exception):
    """Raised when a transition is not allowed from the current state."""

    def __init__(self, current: ReleaseState, trigger: str, guard: str | None = None):
        self.current = current
        self.trigger = trigger
        self.guard = guard
        msg = f"No transition from {current.value} on trigger '{trigger}'"
        if guard:
            msg += f" with guard '{guard}'"
        super().__init__(msg)


class ReleaseStateMachine:
    """Manages the lifecycle state of a single release (track or album).

    The state machine is purely in-memory.  Persistence happens via the
    ``on_transition`` callback which the engine wires to the database layer.
    """

    def __init__(
        self,
        track_id: int,
        initial_state: ReleaseState = ReleaseState.DRAFT,
        on_transition: Any | None = None,
    ) -> None:
        self.track_id = track_id
        self._state = initial_state
        self._on_transition = on_transition
        self._history: list[StateTransitionLog] = []

    @property
    def state(self) -> ReleaseState:
        return self._state

    @property
    def history(self) -> list[StateTransitionLog]:
        return list(self._history)

    def can_transition(self, trigger: str, guard: str | None = None) -> bool:
        """Return True if *trigger* (and optional *guard*) is valid from the current state."""
        candidates = _TRANSITION_INDEX.get((self._state, trigger), [])
        if not candidates:
            return False
        if guard is not None:
            return any(t.guard == guard for t in candidates)
        # If no guard specified, accept if any unguarded or guarded transition exists.
        return True

    def transition(
        self,
        trigger: str,
        changed_by: str = "system",
        reason: str = "",
        guard: str | None = None,
    ) -> StateTransitionLog:
        """Execute a state transition.

        Parameters
        ----------
        trigger:
            The event name causing the transition.
        changed_by:
            Agent or system identifier.
        reason:
            Human-readable reason for the change.
        guard:
            Optional guard tag to disambiguate transitions with the same
            (from_state, trigger) pair.

        Returns
        -------
        StateTransitionLog persisted to history.

        Raises
        ------
        InvalidTransition
            If no matching transition exists.
        """
        candidates = _TRANSITION_INDEX.get((self._state, trigger), [])
        if not candidates:
            raise InvalidTransition(self._state, trigger, guard)

        # Pick the right transition.
        chosen: Transition | None = None
        if guard is not None:
            chosen = next((t for t in candidates if t.guard == guard), None)
        else:
            # Prefer unguarded transitions; fall back to first candidate.
            chosen = next((t for t in candidates if t.guard is None), candidates[0])

        if chosen is None:
            raise InvalidTransition(self._state, trigger, guard)

        old_state = self._state
        self._state = chosen.to_state

        log_entry = StateTransitionLog(
            track_id=self.track_id,
            from_state=old_state,
            to_state=chosen.to_state,
            changed_by=changed_by,
            reason=reason or chosen.description,
        )
        self._history.append(log_entry)

        logger.info(
            "Track %d: %s -> %s (trigger=%s, by=%s)",
            self.track_id,
            old_state.value,
            chosen.to_state.value,
            trigger,
            changed_by,
        )

        if self._on_transition:
            self._on_transition(log_entry)

        return log_entry

    def force_state(self, state: ReleaseState, changed_by: str, reason: str) -> StateTransitionLog:
        """Forcibly set the state — bypasses guards.  Use only for admin overrides."""
        old_state = self._state
        self._state = state
        log_entry = StateTransitionLog(
            track_id=self.track_id,
            from_state=old_state,
            to_state=state,
            changed_by=changed_by,
            reason=f"FORCED: {reason}",
        )
        self._history.append(log_entry)
        logger.warning(
            "Track %d: FORCED %s -> %s by %s: %s",
            self.track_id,
            old_state.value,
            state.value,
            changed_by,
            reason,
        )
        if self._on_transition:
            self._on_transition(log_entry)
        return log_entry

    def get_applicable_timeouts(self, entered_at: datetime, now: datetime | None = None) -> list[TimeoutRule]:
        """Return timeout rules that have fired for the current state."""
        now = now or datetime.utcnow()
        elapsed = now - entered_at
        return [
            rule
            for rule in TIMEOUT_RULES
            if rule.state == self._state and elapsed > rule.max_duration
        ]
