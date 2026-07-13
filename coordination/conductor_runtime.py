"""In-process Studio conductor runtime for the HTTP API process.

This loop keeps conductor queue work inside the existing API runtime instead of
requiring a separate scheduler/service. It is responsible for:

- approving and delivering pending conductor messages
- dispatching due scheduled messages
- generating timeout nags into the scheduled queue
- generating the weekly manager summary
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from coordination.gamification.streaks import StreakTracker
from coordination.gamification.weekly_summary import WeeklySummaryGenerator
from coordination.state_machine import ReleaseState

logger = logging.getLogger(__name__)

_DEFAULT_TIMEZONE = "America/Chicago"
_DEFAULT_QUIET_START = "02:00"
_DEFAULT_QUIET_END = "10:00"
_DEFAULT_WEEKLY_SUMMARY_DAY = 6  # Sunday
_DEFAULT_WEEKLY_SUMMARY_TIME = time(19, 0)
_CONVERSATION_HOLD = timedelta(hours=6)
_NAG_DEDUPE_WINDOW = timedelta(hours=24)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _db_conn(db_path: str) -> sqlite3.Connection:
    conn = _connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _parse_timestamp(raw: Any) -> datetime | None:
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _to_db_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _parse_clock(raw: str | None, default_value: str) -> time:
    text = (raw or default_value).strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        return time(int(hour_text), int(minute_text))
    except (TypeError, ValueError):
        fallback_hour, fallback_minute = default_value.split(":", 1)
        return time(int(fallback_hour), int(fallback_minute))


def _load_json_object(raw: Any) -> dict[str, Any]:
    if raw in (None, ""):
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_context": raw}
    return parsed if isinstance(parsed, dict) else {}


def _load_settings_file(db_path: str) -> dict[str, Any]:
    path = Path(db_path).resolve().parent / "settings.json"
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _model_to_dict(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    if hasattr(model, "json"):
        return json.loads(model.json())
    raise TypeError(f"Unsupported model type: {type(model)!r}")


class StudioConductorRuntime:
    """Background Studio/conductor loop hosted inside the HTTP API process."""

    def __init__(
        self,
        db_path: str,
        *,
        tick_seconds: float = 30.0,
        pipeline_lock: threading.Lock | None = None,
    ) -> None:
        self.db_path = db_path
        self.tick_seconds = tick_seconds
        # Track pipeline runs (intake) do many sequential writes over 60-170s —
        # far longer than this loop's 30s tick. Without a shared lock, every
        # tick opens its own connection and periodically collides with an
        # in-progress pipeline run for the single SQLite writer slot. Sharing
        # the lock serializes the two instead of contending.
        self._pipeline_lock = pipeline_lock
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="studio-conductor-runtime",
            daemon=True,
        )

    def start(self) -> None:
        if self._thread.is_alive():
            return
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def run_cycle(self, now: datetime | None = None) -> dict[str, int]:
        run_now = (now or datetime.now(UTC)).astimezone(UTC)
        lock = self._pipeline_lock if self._pipeline_lock is not None else nullcontext()
        with lock, _db_conn(self.db_path) as conn:
            pending_processed = self._process_pending_messages(conn, run_now)
            nags_enqueued = self._process_timeout_nags(conn, run_now)
            weekly_actions = self._process_weekly_summary(conn, run_now)
            scheduled_processed = self._process_scheduled_messages(conn, run_now)
        return {
            "pending_processed": pending_processed,
            "nags_enqueued": nags_enqueued,
            "weekly_actions": weekly_actions,
            "scheduled_processed": scheduled_processed,
        }

    def _run_loop(self) -> None:
        logger.info(
            "Studio conductor runtime started (db=%s, tick=%.1fs)",
            self.db_path,
            self.tick_seconds,
        )
        while not self._stop_event.is_set():
            try:
                result = self.run_cycle()
                if any(result.values()):
                    logger.info("Studio conductor cycle: %s", result)
            except Exception:
                logger.exception("Studio conductor cycle failed")
            self._stop_event.wait(self.tick_seconds)
        logger.info("Studio conductor runtime stopped")

    def _process_pending_messages(
        self,
        conn: sqlite3.Connection,
        now: datetime,
    ) -> int:
        if not _table_exists(conn, "pending_messages") or not _table_exists(conn, "feedback"):
            return 0

        rows = conn.execute(
            """
            SELECT pm.id,
                   pm.from_agent,
                   pm.draft,
                   pm.refined_draft,
                   pm.context,
                   pm.track_id,
                   t.project_id
              FROM pending_messages pm
              LEFT JOIN tracks t ON t.id = pm.track_id
             WHERE pm.status = 'pending'
             ORDER BY CASE pm.priority
                        WHEN 'urgent' THEN 0
                        WHEN 'high' THEN 1
                        WHEN 'normal' THEN 2
                        WHEN 'low' THEN 3
                        ELSE 4
                      END,
                      pm.submitted_at ASC,
                      pm.id ASC
            """
        ).fetchall()

        if not rows:
            return 0

        delivered = 0
        with conn:
            for row in rows:
                message = str(row["refined_draft"] or row["draft"] or "").strip()
                if not message:
                    logger.warning("Skipping empty pending message id=%s", row["id"])
                    continue
                conn.execute(
                    """
                    INSERT INTO feedback
                        (track_id, project_id, agent, message, channel, direction, intent)
                    VALUES (?, ?, ?, ?, 'desktop', 'outbound', 'studio_queue_delivery')
                    """,
                    (
                        row["track_id"],
                        row["project_id"],
                        row["from_agent"],
                        message,
                    ),
                )
                conn.execute(
                    """
                    UPDATE pending_messages
                       SET status = 'approved',
                           refined_draft = ?,
                           conductor_reasoning = ?,
                           sent_at = ?
                     WHERE id = ?
                    """,
                    (
                        message,
                        "Approved by in-process Studio conductor runtime.",
                        _to_db_timestamp(now),
                        row["id"],
                    ),
                )
                delivered += 1
        return delivered

    def _process_scheduled_messages(
        self,
        conn: sqlite3.Connection,
        now: datetime,
    ) -> int:
        if not _table_exists(conn, "scheduled_messages") or not _table_exists(conn, "feedback"):
            return 0

        local_now, quiet_start, quiet_end = self._local_runtime_clock(conn, now)
        rows = conn.execute(
            """
            SELECT id, agent, channel, message, scheduled_for, context
              FROM scheduled_messages
             WHERE sent_at IS NULL
               AND scheduled_for <= ?
             ORDER BY scheduled_for ASC, id ASC
            """,
            (_to_db_timestamp(now),),
        ).fetchall()

        if not rows:
            return 0

        delivered = 0
        with conn:
            for row in rows:
                context = _load_json_object(row["context"])
                if self._should_hold_for_weekend(local_now, context):
                    defer_to = self._next_business_slot(local_now)
                    conn.execute(
                        "UPDATE scheduled_messages SET scheduled_for = ? WHERE id = ?",
                        (_to_db_timestamp(defer_to.astimezone(UTC)), row["id"]),
                    )
                    continue

                if self._has_active_inbound_conversation(conn, now, context):
                    defer_to = now + timedelta(hours=1)
                    conn.execute(
                        "UPDATE scheduled_messages SET scheduled_for = ? WHERE id = ?",
                        (_to_db_timestamp(defer_to), row["id"]),
                    )
                    continue

                if self._is_quiet_time(local_now, quiet_start, quiet_end):
                    defer_to = self._quiet_window_end(local_now, quiet_end)
                    conn.execute(
                        "UPDATE scheduled_messages SET scheduled_for = ? WHERE id = ?",
                        (_to_db_timestamp(defer_to.astimezone(UTC)), row["id"]),
                    )
                    continue

                conn.execute(
                    """
                    INSERT INTO feedback
                        (track_id, project_id, agent, message, channel, direction, intent)
                    VALUES (?, ?, ?, ?, ?, 'outbound', ?)
                    """,
                    (
                        context.get("track_id"),
                        context.get("project_id"),
                        row["agent"],
                        row["message"],
                        row["channel"],
                        context.get("intent", "scheduled_delivery"),
                    ),
                )
                conn.execute(
                    "UPDATE scheduled_messages SET sent_at = ? WHERE id = ?",
                    (_to_db_timestamp(now), row["id"]),
                )
                delivered += 1
        return delivered

    def _process_timeout_nags(
        self,
        conn: sqlite3.Connection,
        now: datetime,
    ) -> int:
        required_tables = {"tracks", "scheduled_messages"}
        if any(not _table_exists(conn, name) for name in required_tables):
            return 0

        local_now, quiet_start, quiet_end = self._local_runtime_clock(conn, now)
        rows = conn.execute(
            """
            SELECT t.id,
                   t.title,
                   t.state,
                   t.project_id,
                   t.created_at,
                   t.updated_at,
                   p.target_release_date,
                   (
                       SELECT rs.created_at
                         FROM release_states rs
                        WHERE rs.track_id = t.id
                        ORDER BY rs.id DESC
                        LIMIT 1
                   ) AS state_changed_at
              FROM tracks t
              LEFT JOIN projects p ON p.id = t.project_id
             WHERE t.state IN ('FEEDBACK_GIVEN', 'ART_NEEDED', 'RELEASE_READY')
            """
        ).fetchall()

        enqueued = 0
        with conn:
            for row in rows:
                state = str(row["state"]).upper()
                title = str(row["title"] or f"Track {row['id']}")
                entered_at = (
                    _parse_timestamp(row["state_changed_at"])
                    or _parse_timestamp(row["updated_at"])
                    or _parse_timestamp(row["created_at"])
                    or now
                )

                nag_payload: dict[str, Any] | None = None
                if state == ReleaseState.FEEDBACK_GIVEN.value and now - entered_at >= timedelta(days=7):
                    nag_payload = {
                        "agent": "manager",
                        "message": (
                            f"{title} has been sitting on notes for a week. "
                            "Send the next revision or tell me where it is stuck."
                        ),
                        "intent": "nag",
                        "priority": "normal",
                        "timeout_event": "timeout_feedback_stale",
                    }
                elif state == ReleaseState.ART_NEEDED.value and now - entered_at >= timedelta(days=3):
                    nag_payload = {
                        "agent": "creative_director",
                        "message": (
                            f"I still need artwork or clear visual direction for {title} "
                            "before release can move."
                        ),
                        "intent": "nag",
                        "priority": "high",
                        "timeout_event": "timeout_art_overdue",
                    }
                elif state == ReleaseState.RELEASE_READY.value:
                    release_date = _parse_timestamp(row["target_release_date"])
                    if release_date is not None and release_date <= now:
                        nag_payload = {
                            "agent": "manager",
                            "message": (
                                f"{title} missed the planned release window. "
                                "I pulled it off the calendar. Send a new date when you are ready."
                            ),
                            "intent": "nag",
                            "priority": "escalation",
                            "timeout_event": "timeout_release_date_missed",
                        }

                if nag_payload is None:
                    continue

                context = {
                    "type": "timeout_nag",
                    "track_id": row["id"],
                    "project_id": row["project_id"],
                    "intent": nag_payload["intent"],
                    "timeout_event": nag_payload["timeout_event"],
                    "priority": nag_payload["priority"],
                }
                if self._has_recent_scheduled_message(
                    conn,
                    track_id=int(row["id"]),
                    timeout_event=str(nag_payload["timeout_event"]),
                    now=now,
                ):
                    continue

                scheduled_for = now
                if self._should_hold_for_weekend(local_now, context):
                    scheduled_for = self._next_business_slot(local_now).astimezone(UTC)
                elif self._is_quiet_time(local_now, quiet_start, quiet_end):
                    scheduled_for = self._quiet_window_end(local_now, quiet_end).astimezone(UTC)

                conn.execute(
                    """
                    INSERT INTO scheduled_messages (agent, channel, message, scheduled_for, context)
                    VALUES (?, 'sms', ?, ?, ?)
                    """,
                    (
                        nag_payload["agent"],
                        nag_payload["message"],
                        _to_db_timestamp(scheduled_for),
                        json.dumps(context, sort_keys=True),
                    ),
                )
                enqueued += 1
        return enqueued

    def _process_weekly_summary(
        self,
        conn: sqlite3.Connection,
        now: datetime,
    ) -> int:
        required_tables = {"artist_stats", "scheduled_messages", "tracks"}
        if any(not _table_exists(conn, name) for name in required_tables):
            return 0

        local_now, _, _ = self._local_runtime_clock(conn, now)
        due_at_local, period_start_local, period_end_local = self._weekly_summary_window(local_now)
        if local_now < due_at_local:
            return 0

        period_start_utc = period_start_local.astimezone(UTC)
        period_end_utc = period_end_local.astimezone(UTC)
        existing = conn.execute(
            """
            SELECT 1
              FROM artist_stats
             WHERE stat_type = 'weekly_summary'
               AND period_start = ?
               AND period_end = ?
             LIMIT 1
            """,
            (
                period_start_utc.date().isoformat(),
                period_end_utc.date().isoformat(),
            ),
        ).fetchone()
        if existing is not None:
            return 0

        activity = self._weekly_activity_snapshot(conn, period_start_utc, period_end_utc)
        tracked_releases, release_titles, release_dates = self._tracked_release_snapshot(conn, now)
        tracker = StreakTracker()
        tracker.load_uploads(self._track_created_timestamps(conn))
        summary = WeeklySummaryGenerator(streak_tracker=tracker).generate(
            tracked_releases=tracked_releases,
            release_titles=release_titles,
            release_dates=release_dates,
            previous_reputation_points=0,
            now=period_end_utc,
            period_type="weekly",
        )
        summary_dict = _model_to_dict(summary)
        summary_dict["period_start"] = period_start_utc.isoformat().replace("+00:00", "Z")
        summary_dict["period_end"] = period_end_utc.isoformat().replace("+00:00", "Z")
        summary_dict["activity"] = activity
        summary_dict["current_reputation"] = self._current_reputation(conn)

        sent = False
        if sum(activity.values()) > 0:
            message = self._build_weekly_summary_message(summary_dict)
            conn.execute(
                """
                INSERT INTO scheduled_messages (agent, channel, message, scheduled_for, context)
                VALUES (?, 'sms', ?, ?, ?)
                """,
                (
                    "manager",
                    message,
                    _to_db_timestamp(now),
                    json.dumps(
                        {
                            "type": "weekly_summary",
                            "intent": "weekly_summary",
                            "period_start": period_start_utc.date().isoformat(),
                            "period_end": period_end_utc.date().isoformat(),
                        },
                        sort_keys=True,
                    ),
                ),
            )
            sent = True

        conn.execute(
            """
            INSERT INTO artist_stats (stat_type, value, period_start, period_end)
            VALUES (?, ?, ?, ?)
            """,
            (
                "weekly_summary",
                json.dumps(
                    {
                        "sent": sent,
                        "summary": summary_dict,
                    },
                    sort_keys=True,
                ),
                period_start_utc.date().isoformat(),
                period_end_utc.date().isoformat(),
            ),
        )
        return 1

    def _tracked_release_snapshot(
        self,
        conn: sqlite3.Connection,
        now: datetime,
    ) -> tuple[dict[int, tuple[ReleaseState, datetime]], dict[int, str], dict[int, datetime]]:
        tracked_releases: dict[int, tuple[ReleaseState, datetime]] = {}
        release_titles: dict[int, str] = {}
        release_dates: dict[int, datetime] = {}

        rows = conn.execute(
            """
            SELECT t.id,
                   t.title,
                   t.state,
                   t.created_at,
                   t.updated_at,
                   p.target_release_date,
                   (
                       SELECT rs.created_at
                         FROM release_states rs
                        WHERE rs.track_id = t.id
                        ORDER BY rs.id DESC
                        LIMIT 1
                   ) AS state_changed_at
              FROM tracks t
              LEFT JOIN projects p ON p.id = t.project_id
            """
        ).fetchall()
        for row in rows:
            try:
                state = ReleaseState(str(row["state"]).upper())
            except ValueError:
                continue
            entered_at = (
                _parse_timestamp(row["state_changed_at"])
                or _parse_timestamp(row["updated_at"])
                or _parse_timestamp(row["created_at"])
                or now
            )
            track_id = int(row["id"])
            tracked_releases[track_id] = (state, entered_at)
            release_titles[track_id] = str(row["title"] or f"Track {track_id}")
            release_date = _parse_timestamp(row["target_release_date"])
            if release_date is not None:
                release_dates[track_id] = release_date

        return tracked_releases, release_titles, release_dates

    def _track_created_timestamps(self, conn: sqlite3.Connection) -> list[datetime]:
        rows = conn.execute("SELECT created_at FROM tracks ORDER BY created_at ASC").fetchall()
        return [parsed for row in rows if (parsed := _parse_timestamp(row["created_at"])) is not None]

    def _weekly_activity_snapshot(
        self,
        conn: sqlite3.Connection,
        period_start: datetime,
        period_end: datetime,
    ) -> dict[str, int]:
        start_text = _to_db_timestamp(period_start)
        end_text = _to_db_timestamp(period_end)
        snapshot = {
            "tracks_added": 0,
            "feedback_events": 0,
            "release_events": 0,
            "sessions": 0,
        }
        snapshot["tracks_added"] = conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE created_at >= ? AND created_at <= ?",
            (start_text, end_text),
        ).fetchone()[0]
        if _table_exists(conn, "feedback"):
            snapshot["feedback_events"] = conn.execute(
                "SELECT COUNT(*) FROM feedback WHERE created_at >= ? AND created_at <= ?",
                (start_text, end_text),
            ).fetchone()[0]
        if _table_exists(conn, "release_states"):
            snapshot["release_events"] = conn.execute(
                "SELECT COUNT(*) FROM release_states WHERE created_at >= ? AND created_at <= ?",
                (start_text, end_text),
            ).fetchone()[0]
        if _table_exists(conn, "ableton_sessions"):
            snapshot["sessions"] = conn.execute(
                "SELECT COUNT(*) FROM ableton_sessions WHERE started_at >= ? AND started_at <= ?",
                (start_text, end_text),
            ).fetchone()[0]
        elif _table_exists(conn, "session_log"):
            snapshot["sessions"] = conn.execute(
                "SELECT COUNT(*) FROM session_log WHERE started_at >= ? AND started_at <= ?",
                (start_text, end_text),
            ).fetchone()[0]
        return snapshot

    def _current_reputation(self, conn: sqlite3.Connection) -> int:
        if not _table_exists(conn, "artist_stats"):
            return 0
        row = conn.execute(
            """
            SELECT value
              FROM artist_stats
             WHERE stat_type = 'reputation'
             ORDER BY created_at DESC, id DESC
             LIMIT 1
            """
        ).fetchone()
        if row is None:
            return 0
        raw_value = row["value"]
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            payload = _load_json_object(raw_value)
            current = payload.get("total_points") or payload.get("value") or 0
            try:
                return int(current)
            except (TypeError, ValueError):
                return 0

    def _build_weekly_summary_message(self, summary: dict[str, Any]) -> str:
        highlights = list(summary.get("highlights") or [])
        activity = dict(summary.get("activity") or {})

        lines = [
            "Weekly summary:",
            (
                f"{summary.get('tracks_in_progress', 0)} in progress, "
                f"{summary.get('tracks_released', 0)} released, "
                f"{summary.get('tracks_stalled', 0)} stalled."
            ),
        ]
        if activity.get("sessions"):
            lines.append(f"{activity['sessions']} studio session(s) logged.")

        streak_message = str(summary.get("streak_message") or "").strip()
        if streak_message:
            lines.append(streak_message)

        deadlines = summary.get("upcoming_deadlines") or []
        if deadlines:
            next_deadline = deadlines[0]
            title = next_deadline.get("title") or f"Track {next_deadline.get('track_id')}"
            lines.append(
                f"Closest deadline: {title} in {next_deadline.get('days_until', 0)} day(s)."
            )

        if highlights:
            lines.append("Highlights: " + "; ".join(highlights[:3]) + ".")

        reputation = int(summary.get("current_reputation") or 0)
        if reputation > 0:
            lines.append(f"Reputation: {reputation}.")

        return " ".join(part.strip() for part in lines if part.strip())

    def _has_recent_scheduled_message(
        self,
        conn: sqlite3.Connection,
        *,
        track_id: int,
        timeout_event: str,
        now: datetime,
    ) -> bool:
        if not _table_exists(conn, "scheduled_messages"):
            return False
        since = now - _NAG_DEDUPE_WINDOW
        rows = conn.execute(
            """
            SELECT context, sent_at, scheduled_for
              FROM scheduled_messages
             WHERE scheduled_for >= ?
             ORDER BY id DESC
            """,
            (_to_db_timestamp(since),),
        ).fetchall()
        for row in rows:
            context = _load_json_object(row["context"])
            if context.get("type") != "timeout_nag":
                continue
            if int(context.get("track_id") or -1) != track_id:
                continue
            if context.get("timeout_event") != timeout_event:
                continue
            return True
        return False

    def _local_runtime_clock(
        self,
        conn: sqlite3.Connection,
        now: datetime,
    ) -> tuple[datetime, time, time]:
        timezone_name = _DEFAULT_TIMEZONE
        quiet_start_raw = _DEFAULT_QUIET_START
        quiet_end_raw = _DEFAULT_QUIET_END

        settings = _load_settings_file(self.db_path)
        timezone_name = str(settings.get("timezone") or timezone_name)
        quiet_start_raw = str(settings.get("quiet_hours_start") or quiet_start_raw)
        quiet_end_raw = str(settings.get("quiet_hours_end") or quiet_end_raw)

        if _table_exists(conn, "artist_profile"):
            row = conn.execute(
                """
                SELECT timezone, quiet_hours_start, quiet_hours_end
                  FROM artist_profile
                 ORDER BY id ASC
                 LIMIT 1
                """
            ).fetchone()
            if row is not None:
                timezone_name = str(row["timezone"] or timezone_name)
                if not settings.get("quiet_hours_start"):
                    quiet_start_raw = str(row["quiet_hours_start"] or quiet_start_raw)
                if not settings.get("quiet_hours_end"):
                    quiet_end_raw = str(row["quiet_hours_end"] or quiet_end_raw)

        try:
            tz = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo(_DEFAULT_TIMEZONE)

        return (
            now.astimezone(tz),
            _parse_clock(quiet_start_raw, _DEFAULT_QUIET_START),
            _parse_clock(quiet_end_raw, _DEFAULT_QUIET_END),
        )

    def _is_quiet_time(
        self,
        local_now: datetime,
        quiet_start: time,
        quiet_end: time,
    ) -> bool:
        clock = local_now.timetz().replace(tzinfo=None)
        if quiet_start > quiet_end:
            return clock >= quiet_start or clock < quiet_end
        return quiet_start <= clock < quiet_end

    def _quiet_window_end(self, local_now: datetime, quiet_end: time) -> datetime:
        target_date = local_now.date()
        if local_now.timetz().replace(tzinfo=None) >= quiet_end:
            target_date = target_date + timedelta(days=1)
        return datetime.combine(target_date, quiet_end, tzinfo=local_now.tzinfo)

    def _should_hold_for_weekend(
        self,
        local_now: datetime,
        context: dict[str, Any],
    ) -> bool:
        weekday = local_now.weekday()
        if weekday < 5:
            return False
        if context.get("type") == "weekly_summary" and weekday == _DEFAULT_WEEKLY_SUMMARY_DAY:
            return False
        return True

    def _next_business_slot(self, local_now: datetime) -> datetime:
        target = local_now
        while target.weekday() >= 5:
            target = (target + timedelta(days=1)).replace(
                hour=10,
                minute=0,
                second=0,
                microsecond=0,
            )
        if target.weekday() < 5 and target.time() >= time(10, 0):
            target = (target + timedelta(days=1)).replace(
                hour=10,
                minute=0,
                second=0,
                microsecond=0,
            )
        else:
            target = target.replace(hour=10, minute=0, second=0, microsecond=0)
        while target.weekday() >= 5:
            target += timedelta(days=1)
            target = target.replace(hour=10, minute=0, second=0, microsecond=0)
        return target

    def _has_active_inbound_conversation(
        self,
        conn: sqlite3.Connection,
        now: datetime,
        context: dict[str, Any],
    ) -> bool:
        if context.get("priority") in {"urgent", "high", "escalation"}:
            return False
        if not _table_exists(conn, "feedback"):
            return False
        row = conn.execute(
            """
            SELECT direction, created_at
              FROM feedback
             ORDER BY created_at DESC, id DESC
             LIMIT 1
            """
        ).fetchone()
        if row is None or str(row["direction"]) != "inbound":
            return False
        created_at = _parse_timestamp(row["created_at"])
        if created_at is None:
            return False
        return now - created_at <= _CONVERSATION_HOLD

    def _weekly_summary_window(
        self,
        local_now: datetime,
    ) -> tuple[datetime, datetime, datetime]:
        monday = (local_now - timedelta(days=local_now.weekday())).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        due_this_week = monday + timedelta(days=_DEFAULT_WEEKLY_SUMMARY_DAY)
        due_this_week = due_this_week.replace(
            hour=_DEFAULT_WEEKLY_SUMMARY_TIME.hour,
            minute=_DEFAULT_WEEKLY_SUMMARY_TIME.minute,
            second=0,
            microsecond=0,
        )

        if local_now >= due_this_week:
            period_start = monday
            period_end = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
            return due_this_week, period_start, period_end

        previous_monday = monday - timedelta(days=7)
        previous_due = due_this_week - timedelta(days=7)
        period_start = previous_monday
        period_end = previous_monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
        return previous_due, period_start, period_end
