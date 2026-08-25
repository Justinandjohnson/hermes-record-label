"""Run a real audio file through Hermes and verify observable side effects.

The fast tier performs real validation, registration, hashing, event emission,
and vault copying without network credentials.  ``--full-pipeline`` additionally
runs the production dispatcher (OpenRouter analysis, Demucs, feature extraction,
embeddings, and agent reviews).  It never replaces those systems with mocks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from coordination.dispatcher import (
    TrackPipelineDispatcher,
    _named_response_agents,
    _take_is_echo,
)
from file_watcher.validator import validate_audio_file
from file_watcher.watcher import FileWatcherService

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "schema" / "migrations"


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    evidence: str
    kind: str = "action"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _migrate(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        for migration in sorted(MIGRATIONS.glob("*.sql")):
            conn.executescript(migration.read_text(encoding="utf-8"))


def _count(conn: sqlite3.Connection, table: str, track_id: int) -> int:
    return int(
        conn.execute(f"SELECT COUNT(*) FROM {table} WHERE track_id = ?", (track_id,)).fetchone()[0]
    )


def _run_vad_browser_eval(url: str) -> Check:
    """Run the real-speech Live Mode eval in an installed Chrome/Edge browser."""
    node = shutil.which("node")
    script = ROOT / "desktop-app" / "scripts" / "run-vad-eval.mjs"
    if not node:
        return Check("hands_free_voice_turn", False, "node executable not found")
    try:
        completed = subprocess.run(
            [node, str(script), "--url", url],
            cwd=ROOT / "desktop-app",
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
        report = json.loads(completed.stdout)
        passed = completed.returncode == 0 and bool(report.get("passed"))
        return Check("hands_free_voice_turn", passed, json.dumps(report, sort_keys=True))
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return Check("hands_free_voice_turn", False, f"{type(exc).__name__}: {exc}")


def run_action_eval(
    audio_path: str | Path,
    output_root: str | Path,
    *,
    full_pipeline: bool = False,
    max_runtime_seconds: float = 120.0,
    max_agent_words: int = 150,
    max_average_agent_words: float = 90.0,
    check_live_mode: bool = False,
    live_mode_url: str = "http://localhost:8086/?vad_eval=1",
) -> dict[str, Any]:
    """Run the harness and return the JSON-serializable report."""
    run_started = perf_counter()
    source = Path(audio_path).expanduser().resolve()
    stage_started = perf_counter()
    validation = validate_audio_file(source)
    validation_ms = round((perf_counter() - stage_started) * 1000, 1)
    if not validation.is_valid:
        raise ValueError(f"Audio rejected: {validation.rejection_reason}")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + _sha256(source)[:10]
    run_dir = Path(output_root).expanduser().resolve() / run_id
    inbox = run_dir / "inbox"
    vault = run_dir / "vault"
    inbox.mkdir(parents=True, exist_ok=False)
    vault.mkdir()
    db_path = run_dir / "hermes-eval.db"
    _migrate(db_path)
    live_data_dir = Path(
        os.environ.get(
            "AI_RECORD_LABEL_DATA",
            str(Path(os.environ.get("APPDATA", "")) / "ai-record-label"),
        )
    )
    live_settings = live_data_dir / "settings.json"
    if live_settings.is_file():
        # Same voice provider and per-agent voices as the live app, so TTS
        # prewarm here exercises exactly the audio the artist hears.
        shutil.copy2(live_settings, run_dir / "settings.json")

    ingested = inbox / source.name
    shutil.copy2(source, ingested)
    event_journal: list[dict[str, Any]] = []
    pipeline_error: str | None = None
    dispatcher_timings: dict[str, float] = {}

    dispatcher = TrackPipelineDispatcher(str(db_path)) if full_pipeline else None

    def emit(event: str, payload: dict[str, Any]) -> None:
        nonlocal dispatcher_timings, pipeline_error
        entry: dict[str, Any] = {"event": event, "payload": payload}
        event_journal.append(entry)
        if dispatcher is not None:
            entry["result"] = dispatcher(event, payload)
            dispatcher_timings = dict(entry["result"].get("timings_ms") or {})
            if isinstance(entry["result"], dict) and entry["result"].get("error"):
                pipeline_error = str(entry["result"]["error"])

    service = FileWatcherService(
        watch_dir=inbox,
        db_path=db_path,
        emit=emit,
        sync_destinations=[vault],
    )
    conn = service._connect_db()
    pipeline_started_utc = datetime.now(UTC)
    stage_started = perf_counter()
    try:
        service._process_file(str(ingested), conn)
    except Exception as exc:
        pipeline_error = f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()
    intake_pipeline_ms = round((perf_counter() - stage_started) * 1000, 1)

    with sqlite3.connect(db_path) as audit:
        audit.row_factory = sqlite3.Row
        track = audit.execute("SELECT * FROM tracks ORDER BY id DESC LIMIT 1").fetchone()
        track_id = int(track["id"]) if track else 0
        feedback_count = _count(audit, "feedback", track_id) if track else 0
        state_count = _count(audit, "release_states", track_id) if track else 0
        review_rows = (
            audit.execute(
                """SELECT id, agent, intent, message, created_at FROM feedback
                   WHERE track_id = ? AND intent IN (
                     'early_conviction_feedback', 'analysis_feedback',
                     'vision_assessment', 'cultural_authenticity_read',
                     'essential_question_review', 'room_discussion',
                     'review_round_summary'
                   ) ORDER BY id""",
                (track_id,),
            ).fetchall()
            if track
            else []
        )
        act1_rows = [
            row
            for row in review_rows
            if row["intent"]
            in {
                "early_conviction_feedback",
                "analysis_feedback",
                "vision_assessment",
                "cultural_authenticity_read",
                "essential_question_review",
            }
        ]
        room_rows = [row for row in review_rows if row["intent"] == "room_discussion"]
        close_rows = [row for row in review_rows if row["intent"] == "review_round_summary"]
        review_word_counts = [len(str(row["message"]).split()) for row in review_rows]
        review_agents = {str(row["agent"]) for row in review_rows}
        checks = [
            Check(
                "audio_validated", True, f"{validation.format.value}; {validation.file_size} bytes"
            ),
            Check("source_copied_to_inbox", ingested.is_file(), str(ingested)),
            Check(
                "track_registered", track is not None, f"track_id={track_id}" if track else "no row"
            ),
            Check(
                "registered_hash_matches_audio",
                bool(track and track["file_hash"] == _sha256(ingested)),
                str(track["file_hash"] if track else "no row"),
            ),
            Check(
                "event_emitted", bool(event_journal), json.dumps(event_journal, default=str)[:500]
            ),
            Check("vault_copy_created", (vault / source.name).is_file(), str(vault / source.name)),
            Check(
                "vault_copy_hash_matches",
                (vault / source.name).is_file() and _sha256(vault / source.name) == _sha256(source),
                "sha256 equality",
            ),
        ]
        if check_live_mode:
            checks.append(_run_vad_browser_eval(live_mode_url))
        if full_pipeline and track:
            stem = audit.execute(
                "SELECT * FROM track_stems WHERE track_id = ?", (track_id,)
            ).fetchone()
            stem_paths = (
                []
                if stem is None
                else [
                    Path(stem[name])
                    for name in ("vocals_path", "drums_path", "bass_path", "other_path")
                ]
            )
            checks.extend(
                [
                    Check(
                        "audio_analysis_persisted",
                        _count(audit, "audio_analyses", track_id) > 0,
                        "audio_analyses row",
                    ),
                    Check(
                        "four_real_stems_created",
                        len(stem_paths) == 4
                        and all(path.is_file() and path.stat().st_size > 0 for path in stem_paths),
                        json.dumps([str(path) for path in stem_paths]),
                    ),
                    Check(
                        "audio_features_persisted",
                        _count(audit, "track_audio_features", track_id) > 0,
                        "track_audio_features row",
                    ),
                    Check(
                        "embedding_persisted",
                        _count(audit, "track_audio_embeddings", track_id) > 0,
                        "track_audio_embeddings row",
                    ),
                    Check(
                        "segments_persisted",
                        _count(audit, "track_segments", track_id) > 0,
                        "track_segments rows",
                    ),
                    Check(
                        "pipeline_completed_without_error",
                        pipeline_error is None,
                        pipeline_error or "dispatcher completed",
                    ),
                    Check(
                        "release_state_changed",
                        state_count > 0,
                        f"{state_count} release_states rows",
                    ),
                    Check(
                        "music_execs_gave_first_reads",
                        {str(row["agent"]) for row in act1_rows}
                        == {"kallman", "a_and_r", "janick", "rhone", "rubin"},
                        json.dumps(sorted(str(row["agent"]) for row in act1_rows)),
                    ),
                    Check(
                        "room_session_took_place",
                        len(room_rows) >= 3 and len({str(row["agent"]) for row in room_rows}) >= 2,
                        f"{len(room_rows)} room takes from "
                        + json.dumps(sorted(str(row["agent"]) for row in room_rows)),
                    ),
                    Check(
                        "manager_closed_the_room",
                        len(close_rows) >= 1 and all(
                            str(row["agent"]) == "manager" for row in close_rows
                        ),
                        f"{len(close_rows)} close-out(s) from the manager",
                    ),
                    Check(
                        "first_reads_include_cinematic_line",
                        bool(act1_rows)
                        and all(
                            "visually:" in str(row["message"]).lower() for row in act1_rows
                        ),
                        json.dumps(
                            [
                                str(row["agent"])
                                for row in act1_rows
                                if "visually:" not in str(row["message"]).lower()
                            ]
                        ),
                    ),
                ]
            )
            ordered_review = sorted(review_rows, key=lambda row: str(row["created_at"]))
            room_sequence = [row for row in ordered_review if row["intent"] == "room_discussion"]
            unnamed = [
                str(row["agent"])
                for row in room_sequence[1:]
                if not set(_named_response_agents(str(row["message"]))).difference(
                    str(row["agent"])
                )
            ]
            # The manager's close is a deliberate summary of the decision, not a new
            # take, so the dispatcher skips its echo gate. Mirror that here.
            echoed_takes = [row for row in ordered_review if row["intent"] != "review_round_summary"]
            echo_violations = [
                str(row["agent"])
                for i, row in enumerate(echoed_takes)
                if _take_is_echo(
                    str(row["message"]),
                    [str(prior["message"]) for prior in echoed_takes[:i]],
                )
            ]
            visual_lines: dict[str, str] = {}
            visual_duplicates: list[str] = []
            for row in ordered_review:
                text = str(row["message"]).lower()
                idx = text.find("visually:")
                if idx == -1:
                    continue
                line = text[idx:].split("\n", 1)[0].strip()
                if not line:
                    continue
                if line in visual_lines:
                    visual_duplicates.append(
                        f"{row['agent']} repeats {visual_lines[line]}'s visual"
                    )
                else:
                    visual_lines[line] = str(row["agent"])
            close_text = " ".join(str(row["message"]) for row in close_rows).lower()
            decision_keywords = (
                "verdict",
                "decision",
                "making the call",
                "the call",
                "ship",
                "revise",
                "approve",
                "next move",
            )
            tts_dir = run_dir / "tts"

            def tts_cached(row: sqlite3.Row) -> bool:
                if not tts_dir.is_dir():
                    return False
                prefix = f"msg-{row['id']}-"
                return any(
                    part.name.startswith(prefix) and not part.name.endswith(".type")
                    for part in tts_dir.iterdir()
                )

            tts_missing = [
                f"{row['agent']}#{row['id']}" for row in review_rows if not tts_cached(row)
            ]
            compared_room = max(len(room_sequence) - 1, 1)
            checks.extend(
                [
                    Check(
                        "tts_prewarmed",
                        bool(review_rows) and not tts_missing,
                        json.dumps(tts_missing)
                        if tts_missing
                        else f"{len(review_rows)} voices cached before playback",
                    ),
                    Check(
                        "room_takes_address_a_peer",
                        len(room_sequence) >= 2
                        and len(unnamed) <= compared_room // 5,
                        f"{len(room_sequence) - 1 - len(unnamed)}/{compared_room} room takes "
                        f"name a peer; unnamed={json.dumps(unnamed)}",
                        kind="quality",
                    ),
                    Check(
                        "no_echo_takes_in_meeting",
                        not echo_violations,
                        json.dumps(echo_violations)
                        if echo_violations
                        else "all takes distinct",
                        kind="quality",
                    ),
                    Check(
                        "first_reads_have_distinct_visuals",
                        not visual_duplicates,
                        json.dumps(visual_duplicates)
                        if visual_duplicates
                        else "all visual lines unique",
                        kind="quality",
                    ),
                    Check(
                        "close_states_a_decision",
                        bool(close_rows) and any(k in close_text for k in decision_keywords),
                        close_text[:160],
                        kind="quality",
                    ),
                ]
            )
            average_words = (
                sum(review_word_counts) / len(review_word_counts) if review_word_counts else 0.0
            )
            checks.extend(
                [
                    Check(
                        "agent_messages_within_word_limit",
                        bool(review_word_counts) and max(review_word_counts) <= max_agent_words,
                        f"max={max(review_word_counts, default=0)}; budget={max_agent_words}",
                        kind="quality",
                    ),
                    Check(
                        "agent_messages_concise_on_average",
                        bool(review_word_counts) and average_words <= max_average_agent_words,
                        f"average={average_words:.1f}; budget={max_average_agent_words:.1f}",
                        kind="quality",
                    ),
                ]
            )

    action_checks = [check for check in checks if check.kind == "action"]
    passed_actions = sum(check.passed for check in action_checks)
    verdict = "action_backed" if all(check.passed for check in action_checks) else "partial_action"
    if passed_actions == 0 and feedback_count:
        verdict = "talk_only"
    total_ms = round((perf_counter() - run_started) * 1000, 1)
    quality_checks = [check for check in checks if check.kind == "quality"]
    quality_checks.append(
        Check(
            "runtime_within_budget",
            total_ms <= max_runtime_seconds * 1000,
            f"runtime={total_ms / 1000:.2f}s; budget={max_runtime_seconds:.2f}s",
            kind="quality",
        )
    )
    checks = [check for check in checks if check.kind != "quality"] + quality_checks
    first_voice_seconds: float | None = None
    if review_rows:
        first_created = min(str(row["created_at"]) for row in review_rows)
        first_voice_dt = datetime.strptime(first_created, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        first_voice_seconds = round((first_voice_dt - pipeline_started_utc).total_seconds(), 1)
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "mode": "full_pipeline" if full_pipeline else "local_action",
        "source": {"path": str(source), "sha256": _sha256(source), "bytes": source.stat().st_size},
        "run_dir": str(run_dir),
        "pipeline_error": pipeline_error,
        "checks": [asdict(check) for check in checks],
        "metrics": {
            "verified_actions": passed_actions,
            "required_actions": len(action_checks),
            "agent_messages": feedback_count,
            "state_transitions": state_count,
            "review_message_words": review_word_counts,
            "average_review_message_words": round(
                sum(review_word_counts) / len(review_word_counts), 1
            )
            if review_word_counts
            else 0.0,
            "time_to_first_voice_seconds": first_voice_seconds,
        },
        "timings_ms": {
            "validation": validation_ms,
            "intake_and_pipeline": intake_pipeline_ms,
            "total": total_ms,
            **dispatcher_timings,
        },
        "verdict": verdict,
        "quality_verdict": (
            "within_budgets"
            if all(check.passed for check in quality_checks)
            else "needs_improvement"
        ),
    }
    (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", required=True, help="Real MP3/WAV/FLAC/AIFF/OGG file")
    parser.add_argument("--output-dir", default="eval-results", help="Persistent eval output root")
    parser.add_argument(
        "--full-pipeline", action="store_true", help="Run live models, stems, and agents"
    )
    parser.add_argument("--max-runtime-seconds", type=float, default=120.0)
    parser.add_argument("--max-agent-words", type=int, default=150)
    parser.add_argument("--max-average-agent-words", type=float, default=90.0)
    parser.add_argument(
        "--check-live-mode",
        action="store_true",
        help="Feed real speech through browser VAD and verify automatic turn closure",
    )
    parser.add_argument(
        "--live-mode-url",
        default="http://localhost:8086/?vad_eval=1",
        help="Running app URL used by --check-live-mode",
    )
    args = parser.parse_args()
    report = run_action_eval(
        args.audio,
        args.output_dir,
        full_pipeline=args.full_pipeline,
        max_runtime_seconds=args.max_runtime_seconds,
        max_agent_words=args.max_agent_words,
        max_average_agent_words=args.max_average_agent_words,
        check_live_mode=args.check_live_mode,
        live_mode_url=args.live_mode_url,
    )
    print(json.dumps(report, indent=2))
    return 0 if report["verdict"] == "action_backed" else 1


if __name__ == "__main__":
    sys.exit(main())
