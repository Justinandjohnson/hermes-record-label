"""Model latency + quality battery for the Hermes pipeline.

Runs the REAL production calls with swapped model IDs against real audio and a
real track's prompt context:

  Audio:  analyze() x N samples + analyze_segments() x 1, per candidate,
          in an isolated temp DB (never touches the live database).
  Text:   _generate_agent_message_async() x N samples per candidate, using the
          live track's real prompt context (read-only).

Sequential by design - concurrent OpenRouter calls get throttled on this
account (in-flight budget), which would corrupt the latency numbers.
Quality gates: analysis must parse, segments must validate, takes are scored
by the roundtable harness contract (stance/evidence/action/length).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import shutil
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from action_harness import _migrate, _sha256  # noqa: E402
from audio_analysis.analyzer import analyze  # noqa: E402
from audio_analysis.gemini_client import _openrouter_key  # noqa: E402
from audio_analysis.segment_analyzer import analyze_segments  # noqa: E402
from coordination.dispatcher import (  # noqa: E402
    _generate_agent_message_async,
    _latest_analysis,
    _track_prompt_context,
)
from roundtable_harness import message_contract_score  # noqa: E402

AUDIO_MODELS = [
    "google/gemini-3.1-pro-preview",  # current baseline
    "google/gemini-3.7-flash",
    "google/gemini-3.1-flash-lite",
    "google/gemini-2.5-flash",
]
TEXT_MODELS = [
    "qwen/qwen3.8-27b",  # current baseline
    "qwen/qwen3.7-flash",
    "qwen/qwen3.6-flash",
    "google/gemini-2.5-flash",
]

LIVE_DATA_DIR = Path(
    os.environ.get(
        "AI_RECORD_LABEL_DATA",
        str(Path(os.environ.get("APPDATA", "")) / "ai-record-label"),
    )
)


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def _insert_track(conn: sqlite3.Connection, file_path: Path) -> int:
    cur = conn.execute(
        """INSERT INTO tracks (title, file_path, file_hash, format, state, version)
           VALUES (?, ?, ?, ?, 'DRAFT', 1)""",
        (
            file_path.stem,
            str(file_path),
            _sha256(file_path),
            file_path.suffix.lstrip("."),
        ),
    )
    return int(cur.lastrowid)


def run_audio_battery(audio_file: Path, out_dir: Path, samples: int) -> list[dict[str, Any]]:
    work = out_dir / "audio-work"
    work.mkdir(parents=True, exist_ok=True)
    db_path = work / "battery.db"
    if db_path.exists():
        db_path.unlink()
    _migrate(db_path)
    copied = work / audio_file.name
    shutil.copy2(audio_file, copied)
    with sqlite3.connect(db_path) as conn:
        track_id = _insert_track(conn, copied)

    results: list[dict[str, Any]] = []
    for model in AUDIO_MODELS:
        entry: dict[str, Any] = {
            "model": model,
            "analysis_ms": [],
            "segments_ms": None,
            "segments_count": None,
            "errors": [],
        }
        for i in range(samples):
            try:
                started = perf_counter()
                analysis = analyze(str(copied), str(db_path), track_id=track_id, model=model)
                entry["analysis_ms"].append(round((perf_counter() - started) * 1000, 1))
                if not analysis.structure:
                    entry["errors"].append(f"analysis#{i}: empty structure (raw={len(analysis.raw_response or '')} chars)")
            except Exception as exc:
                entry["errors"].append(f"analysis#{i}: {type(exc).__name__}: {str(exc)[:300]}")
        try:
            started = perf_counter()
            segments = asyncio.run(analyze_segments(str(copied), str(db_path), track_id, model=model))
            entry["segments_ms"] = round((perf_counter() - started) * 1000, 1)
            entry["segments_count"] = len(segments)
        except Exception as exc:
            entry["errors"].append(f"segments: {type(exc).__name__}: {str(exc)[:300]}")
        entry["analysis_median_ms"] = _median(entry["analysis_ms"])
        results.append(entry)
    return results


def run_text_battery(track_id: int, out_dir: Path, samples: int) -> dict[str, Any]:
    db_path = LIVE_DATA_DIR / "hermes.db"
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        track = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        analysis = _latest_analysis(conn, track_id)
        if track is None or analysis is None:
            raise SystemExit(f"track {track_id} has no analysis in the live DB")
        prompt_context = _track_prompt_context(
            conn, track=track, project_id=None, analysis=analysis,
            stage="post_analysis_review_round",
        )
    finally:
        conn.close()

    api_key = _openrouter_key(os.environ.get("OPENROUTER_API_KEY"))
    results: list[dict[str, Any]] = []
    for model in TEXT_MODELS:
        entry: dict[str, Any] = {
            "model": model,
            "take_ms": [],
            "contract_scores": [],
            "takes": [],
            "errors": [],
        }
        for i in range(samples):
            try:
                started = perf_counter()
                message, _ts = asyncio.run(
                    _generate_agent_message_async(
                        agent="a_and_r",
                        prompt_context=prompt_context,
                        model=model,
                        api_key=api_key,
                        audience="artist",
                    )
                )
                elapsed_ms = round((perf_counter() - started) * 1000, 1)
                score, notes = message_contract_score(message)
                entry["take_ms"].append(elapsed_ms)
                entry["contract_scores"].append(score)
                entry["takes"].append({"sample": i, "ms": elapsed_ms, "notes": notes, "message": message})
            except Exception as exc:
                entry["errors"].append(f"take#{i}: {type(exc).__name__}: {str(exc)[:300]}")
        entry["take_median_ms"] = _median(entry["take_ms"])
        entry["contract_avg"] = (
            round(sum(entry["contract_scores"]) / len(entry["contract_scores"]), 2)
            if entry["contract_scores"]
            else None
        )
        results.append(entry)
    return {"track_id": track_id, "prompt_context_chars": len(prompt_context), "results": results}


def print_report(report: dict[str, Any]) -> None:
    print(f"\n=== AUDIO (real track, median of {report.get('samples')} samples) ===")
    print(f"{'model':38s} {'analysis':>12s} {'segments':>12s} {'segs#':>6s}  errors")
    for entry in report.get("audio", []):
        analysis = f"{entry['analysis_median_ms']}ms" if entry["analysis_median_ms"] else "n/a"
        segments = f"{entry['segments_ms']}ms" if entry["segments_ms"] else "n/a"
        count = str(entry["segments_count"]) if entry["segments_count"] else "-"
        errors = "; ".join(entry["errors"]) or "-"
        print(f"{entry['model']:38s} {analysis:>12s} {segments:>12s} {count:>6s}  {errors[:120]}")

    text = report.get("text") or {}
    if text:
        print(f"\n=== TEXT (real prompt context, {text['prompt_context_chars']} chars, median of {report.get('samples')} samples) ===")
        print(f"{'model':38s} {'take':>12s} {'contract':>9s}  errors")
        for entry in text["results"]:
            take = f"{entry['take_median_ms']}ms" if entry["take_median_ms"] else "n/a"
            contract = str(entry["contract_avg"]) if entry["contract_avg"] is not None else "n/a"
            errors = "; ".join(entry["errors"]) or "-"
            print(f"{entry['model']:38s} {take:>12s} {contract:>9s}  {errors[:120]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", action="store_true", help="run the audio model battery")
    parser.add_argument("--text", action="store_true", help="run the text model battery")
    parser.add_argument(
        "--audio-file",
        default=str(LIVE_DATA_DIR / "inbox" / "treees.mp3"),
        help="real audio file for the audio battery",
    )
    parser.add_argument("--track-id", type=int, default=2, help="live track for the text prompt context")
    parser.add_argument("--samples", type=int, default=3)
    args = parser.parse_args()
    if not args.audio and not args.text:
        args.audio = args.text = True

    out_dir = ROOT / "eval-results" / f"model-battery-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    out_dir.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {
        "schema_version": 1,
        "audio_file": str(Path(args.audio_file).resolve()) if args.audio else None,
        "samples": args.samples,
        "started": datetime.now(UTC).isoformat(),
    }

    if args.audio:
        report["audio"] = run_audio_battery(Path(args.audio_file).expanduser().resolve(), out_dir, args.samples)
    if args.text:
        report["text"] = run_text_battery(args.track_id, out_dir, args.samples)

    report["finished"] = datetime.now(UTC).isoformat()
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print_report(report)
    print(f"\nreport: {out_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
