"""Timeout-based nag rules — generates follow-up messages when releases stall.

Checks for timed-out states periodically, generates nag messages in the
appropriate agent's voice, and respects DND / quiet hours.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any

from pydantic import BaseModel, Field

from coordination.state_machine import ReleaseState, TimeoutRule, TIMEOUT_RULES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class NagMessage(BaseModel):
    """A nag message to be sent via an agent."""

    track_id: int
    agent: str
    message_template: str
    channel: str = "sms"
    priority: str = Field(default="normal", description="normal, high, escalation")
    metadata: dict[str, Any] = Field(default_factory=dict)
    scheduled_for: datetime | None = None


class QuietHours(BaseModel):
    """DND window — nags generated during this window are deferred."""

    start: time = time(22, 0)  # 10 PM
    end: time = time(9, 0)     # 9 AM
    timezone: str = "America/Los_Angeles"


class StalledRelease(BaseModel):
    """A release that has been in a state too long."""

    track_id: int
    state: ReleaseState
    entered_at: datetime
    elapsed: timedelta
    timeout_rule: TimeoutRule


# ---------------------------------------------------------------------------
# Nag templates per agent
# ---------------------------------------------------------------------------

_NAG_TEMPLATES: dict[str, dict[str, str]] = {
    "timeout_feedback_stale": {
        "agent": "manager",
        "template": "feedback_stale_nag",
        "priority": "normal",
    },
    "timeout_art_overdue": {
        "agent": "creative_director",
        "template": "art_overdue_escalation",
        "priority": "high",
    },
    "timeout_release_date_missed": {
        "agent": "manager",
        "template": "missed_release_date",
        "priority": "escalation",
    },
}


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class NagScheduler:
    """Scans for stalled releases and generates appropriately-voiced nag messages.

    Parameters
    ----------
    db:
        Async database interface with:
        - ``get_stalled_releases() -> list[tuple[int, ReleaseState, datetime]]``
        - ``get_nag_history(track_id, event) -> list[datetime]``
        - ``schedule_message(msg: NagMessage) -> None``
    quiet_hours:
        DND configuration.
    min_nag_interval:
        Minimum time between nags for the same track+event.
    """

    def __init__(
        self,
        db: Any = None,
        quiet_hours: QuietHours | None = None,
        min_nag_interval: timedelta = timedelta(days=1),
    ) -> None:
        self._db = db
        self._quiet_hours = quiet_hours or QuietHours()
        self._min_nag_interval = min_nag_interval
        self._nag_history: dict[tuple[int, str], datetime] = {}

    def is_quiet_time(self, now: datetime | None = None) -> bool:
        """Check if the current time falls within quiet hours."""
        now = now or datetime.utcnow()
        current_time = now.time()

        start = self._quiet_hours.start
        end = self._quiet_hours.end

        # Handle overnight windows (e.g. 22:00 -> 09:00).
        if start > end:
            return current_time >= start or current_time <= end
        return start <= current_time <= end

    def _should_nag(self, track_id: int, timeout_event: str, now: datetime) -> bool:
        """Check if enough time has passed since the last nag for this track+event."""
        key = (track_id, timeout_event)
        last_nag = self._nag_history.get(key)
        if last_nag is None:
            return True
        return (now - last_nag) >= self._min_nag_interval

    def _record_nag(self, track_id: int, timeout_event: str, now: datetime) -> None:
        """Record that a nag was sent."""
        self._nag_history[(track_id, timeout_event)] = now

    async def check_and_generate(
        self,
        stalled: list[StalledRelease],
        now: datetime | None = None,
    ) -> list[NagMessage]:
        """Check stalled releases and generate nag messages.

        Parameters
        ----------
        stalled:
            List of releases that have exceeded their timeout thresholds.
        now:
            Current time (defaults to utcnow).

        Returns
        -------
        List of NagMessage objects to be dispatched.  Messages during quiet
        hours are deferred to the end of the quiet window.
        """
        now = now or datetime.utcnow()
        messages: list[NagMessage] = []

        for release in stalled:
            event = release.timeout_rule.timeout_event
            template_info = _NAG_TEMPLATES.get(event)
            if template_info is None:
                logger.warning("No nag template for timeout event '%s'", event)
                continue

            if not self._should_nag(release.track_id, event, now):
                logger.debug(
                    "Skipping nag for track %d (%s) — too recent",
                    release.track_id,
                    event,
                )
                continue

            scheduled_for: datetime | None = None
            if self.is_quiet_time(now):
                # Defer to end of quiet window.
                end = self._quiet_hours.end
                tomorrow = now.date() + timedelta(days=1)
                scheduled_for = datetime.combine(
                    tomorrow if now.time() > end else now.date(),
                    end,
                )
                logger.info(
                    "Deferring nag for track %d to %s (quiet hours)",
                    release.track_id,
                    scheduled_for,
                )

            msg = NagMessage(
                track_id=release.track_id,
                agent=template_info["agent"],
                message_template=template_info["template"],
                priority=template_info["priority"],
                scheduled_for=scheduled_for,
                metadata={
                    "state": release.state.value,
                    "elapsed_days": release.elapsed.days,
                    "timeout_event": event,
                },
            )
            messages.append(msg)
            self._record_nag(release.track_id, event, now)

            # Persist to database if available.
            if self._db is not None:
                try:
                    await self._db.schedule_message(msg)
                except Exception:
                    logger.exception("Failed to persist nag for track %d", release.track_id)

        return messages

    async def scan_and_nag(
        self,
        tracked_releases: dict[int, tuple[ReleaseState, datetime]],
        now: datetime | None = None,
    ) -> list[NagMessage]:
        """High-level scan: find stalled releases and generate nags.

        Parameters
        ----------
        tracked_releases:
            Mapping of track_id -> (current_state, entered_at).
        """
        now = now or datetime.utcnow()
        stalled: list[StalledRelease] = []

        for track_id, (state, entered_at) in tracked_releases.items():
            elapsed = now - entered_at
            for rule in TIMEOUT_RULES:
                if rule.state == state and elapsed > rule.max_duration:
                    stalled.append(
                        StalledRelease(
                            track_id=track_id,
                            state=state,
                            entered_at=entered_at,
                            elapsed=elapsed,
                            timeout_rule=rule,
                        )
                    )

        if stalled:
            logger.info("Found %d stalled release(s)", len(stalled))

        return await self.check_and_generate(stalled, now)
