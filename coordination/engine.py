"""Main Hermes event processor — the coordination engine.

Receives events from Hermes (file watcher, agents, Bandcamp sidecar, timeouts)
and dispatches them through the rules engine to drive state transitions and
agent actions.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from coordination.rules import ActionResult, apply_rules
from coordination.state_machine import (
    InvalidTransition,
    ReleaseState,
    ReleaseStateMachine,
    StateTransitionLog,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------

class HermesEvent(BaseModel):
    """An inbound event from Hermes or internal subsystem."""

    event_type: str = Field(description="e.g. new_track_detected, audio_analysis_complete")
    track_id: int | None = None
    project_id: int | None = None
    agent: str | None = Field(default=None, description="Originating agent id")
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class EventLog(BaseModel):
    """Persisted event log entry."""

    event: HermesEvent
    transition: StateTransitionLog | None = None
    actions: list[ActionResult] = Field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class CoordinationEngine:
    """Central event processor that connects agents through the release pipeline.

    The engine:
    1. Receives ``HermesEvent`` objects
    2. Looks up the release's current state machine
    3. Applies coordination rules to determine actions + transitions
    4. Executes actions (sends messages, triggers analysis, etc.)
    5. Logs everything

    Parameters
    ----------
    db:
        An async database interface (duck-typed) with methods:
        - ``get_release_state(track_id) -> tuple[ReleaseState, datetime]``
        - ``log_transition(entry: StateTransitionLog) -> None``
        - ``log_event(entry: EventLog) -> None``
    bandcamp_bridge:
        ``BandcampBridge`` instance for Bandcamp API calls.
    nag_scheduler:
        ``NagScheduler`` instance for timeout-based nag generation.
    """

    def __init__(
        self,
        db: Any = None,
        bandcamp_bridge: Any = None,
        nag_scheduler: Any = None,
    ) -> None:
        self._db = db
        self._bandcamp = bandcamp_bridge
        self._nag_scheduler = nag_scheduler
        self._machines: dict[int, ReleaseStateMachine] = {}
        self._event_log: list[EventLog] = []

    # ------------------------------------------------------------------
    # State machine management
    # ------------------------------------------------------------------

    async def _get_machine(self, track_id: int) -> ReleaseStateMachine:
        """Retrieve or create the state machine for a track."""
        if track_id in self._machines:
            return self._machines[track_id]

        # Try loading from database.
        initial_state = ReleaseState.DRAFT
        if self._db is not None:
            try:
                state, _entered_at = await self._db.get_release_state(track_id)
                initial_state = state
            except Exception:
                logger.debug("No existing state for track %d, starting at DRAFT", track_id)

        machine = ReleaseStateMachine(
            track_id=track_id,
            initial_state=initial_state,
            on_transition=self._persist_transition,
        )
        self._machines[track_id] = machine
        return machine

    def _persist_transition(self, entry: StateTransitionLog) -> None:
        """Callback wired into each state machine to persist transitions."""
        if self._db is not None:
            # Fire-and-forget; the engine's process_event awaits the full pipeline.
            import asyncio

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._db.log_transition(entry))
            except RuntimeError:
                logger.warning("No event loop — transition for track %d not persisted", entry.track_id)

    # ------------------------------------------------------------------
    # Core event processing
    # ------------------------------------------------------------------

    async def process_event(self, event: HermesEvent) -> EventLog:
        """Process a single Hermes event end-to-end.

        Returns an ``EventLog`` with the transition (if any) and list of
        actions that were dispatched.
        """
        log_entry = EventLog(event=event)

        track_id = event.track_id
        if track_id is None:
            logger.warning("Event %s has no track_id — skipping state machine", event.event_type)
            self._event_log.append(log_entry)
            return log_entry

        machine = await self._get_machine(track_id)

        # Apply coordination rules to determine what should happen.
        try:
            actions = await apply_rules(
                event=event,
                machine=machine,
                bandcamp_bridge=self._bandcamp,
            )
            log_entry.actions = actions
        except InvalidTransition as exc:
            log_entry.error = str(exc)
            logger.error("Invalid transition for track %d: %s", track_id, exc)
        except Exception as exc:
            log_entry.error = f"Rule execution failed: {exc}"
            logger.exception("Unexpected error processing event %s for track %d", event.event_type, track_id)

        # Capture the most recent transition from history.
        if machine.history:
            log_entry.transition = machine.history[-1]

        # Persist event log.
        if self._db is not None:
            try:
                await self._db.log_event(log_entry)
            except Exception:
                logger.exception("Failed to persist event log for track %d", track_id)

        self._event_log.append(log_entry)
        return log_entry

    # ------------------------------------------------------------------
    # Bulk / batch
    # ------------------------------------------------------------------

    async def process_events(self, events: list[HermesEvent]) -> list[EventLog]:
        """Process multiple events sequentially."""
        results: list[EventLog] = []
        for event in events:
            result = await self.process_event(event)
            results.append(result)
        return results

    # ------------------------------------------------------------------
    # Timeout scanning
    # ------------------------------------------------------------------

    async def check_timeouts(self) -> list[HermesEvent]:
        """Scan all tracked releases for timeout conditions and generate events."""
        timeout_events: list[HermesEvent] = []

        for track_id, machine in self._machines.items():
            # Determine when the current state was entered.
            entered_at = datetime.utcnow()
            if machine.history:
                entered_at = machine.history[-1].created_at

            if self._db is not None:
                try:
                    _, entered_at = await self._db.get_release_state(track_id)
                except Exception:
                    pass

            fired = machine.get_applicable_timeouts(entered_at)
            for rule in fired:
                evt = HermesEvent(
                    event_type=rule.timeout_event,
                    track_id=track_id,
                    agent="system",
                    payload={"rule": rule.description},
                )
                timeout_events.append(evt)

        return timeout_events

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_state(self, track_id: int) -> ReleaseState | None:
        """Return the current state of a track, or None if not tracked."""
        machine = self._machines.get(track_id)
        return machine.state if machine else None

    @property
    def event_log(self) -> list[EventLog]:
        return list(self._event_log)

    @property
    def tracked_tracks(self) -> dict[int, ReleaseState]:
        return {tid: m.state for tid, m in self._machines.items()}
