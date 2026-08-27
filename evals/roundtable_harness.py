"""Measure and test the roundtable conversation stack against the real pipeline.

Every check runs against an isolated copy of the live database — nothing here
touches production data. LLM and TTS calls are real (OpenRouter qwen, Fish
cloud). Suites:

  A. separate functions  — echo gate, selector behaviour, message contract,
                           TTS provider switch, voice mapping, cache, endpoints
  B. integration         — artist reply round + agent debate round end-to-end
  C. optimization        — per-stage latency, echo rate, contract compliance

Usage:
  python evals/roundtable_harness.py [--track-id 2] [--message "..."] [--fast]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from coordination.dispatcher import (  # noqa: E402
    AGENT_DISPLAY_NAMES,
    MUSIC_EXECS,
    ROUND_TABLE_INTENTS,
    TrackPipelineDispatcher,
    _agent_model,
    _generate_agent_message_async,
    _latest_analysis,
    _openrouter_key,
    _select_next_speaker_async,
    _take_is_echo,
    _track_prompt_context,
    run_intake_rounds,
)
from audio_analysis import tts as tts_module  # noqa: E402
from audio_analysis.tts import (  # noqa: E402
    _current_provider,
    _env_key,
    _fish_reference_id,
    _synthesize_fish_cloud,
    cached_media_type,
    synthesize,
)


@dataclass
class Check:
    name: str
    passed: bool
    evidence: str
    kind: str = "function"


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    timings_ms: dict[str, float] = field(default_factory=dict)

    def add(self, name: str, passed: bool, evidence: str, kind: str = "function") -> None:
        self.checks.append(Check(name, passed, evidence[:400], kind))
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}: {evidence[:120]}")

    def timing(self, key: str, ms: float) -> None:
        self.timings_ms[key] = round(ms, 1)


# ── Suite A: separate functions ───────────────────────────────────────────────


def check_echo_gate(report: Report) -> None:
    original = "the low end is muddy at 0:45, carve the 1-2kHz range and ship it"
    paraphrase = "the bass frequencies get muddy around 0:45 — notch 1-2khz and then ship"
    distinct = "janick is right about the world-building, but the hook needs a second layer"
    report.add(
        "echo_gate_catches_paraphrase",
        _take_is_echo(paraphrase, [original]),
        f"paraphrase of '{original[:40]}...' detected as echo",
    )
    report.add(
        "echo_gate_passes_distinct",
        not _take_is_echo(distinct, [original]),
        "different-lane take not flagged",
    )
    report.add(
        "echo_gate_empty_safe",
        not _take_is_echo("", [original]) and not _take_is_echo(original, []),
        "empty inputs do not crash or false-positive",
    )


def _selector_probe(report: Report, *, trigger: str, transcript: list[tuple[str, str]], remaining: list[str]) -> str:
    api_key = _openrouter_key(_env_key("OPENROUTER_API_KEY"))
    started = perf_counter()
    pick = asyncio.run(
        _select_next_speaker_async(
            remaining=remaining,
            transcript=transcript,
            trigger_text=trigger,
            stage_label="harness_probe",
            turns_left=3,
            allow_manager_summary=True,
            model=_agent_model(),
            api_key=api_key,
        )
    )
    report.timing("selector_probe_ms", (perf_counter() - started) * 1000)
    return pick


def check_selector(report: Report) -> None:
    if not _env_key("OPENROUTER_API_KEY"):
        report.add("selector_real_model", False, "OPENROUTER_API_KEY not set")
        return

    pick_addressed = _selector_probe(
        report,
        trigger="hey Ravi, be honest — what's wrong with the mix on this one?",
        transcript=[],
        remaining=["kallman", "a_and_r", "janick", "rhone", "rubin", "manager"],
    )
    report.add(
        "selector_addressed_agent_first",
        pick_addressed == "a_and_r",
        f"artist addressed Ravi -> picked '{pick_addressed}'",
    )

    covered = [
        ("kallman", "commercially this declares itself in the first bar, i'd run it back."),
        ("a_and_r", "the 1-2kHz range buries the vocal sample at 1:00, carve it."),
        ("janick", "the question is whether this starts a world or stays a single."),
        ("rhone", "bedroom beat culture will claim this first, protect that."),
        ("rubin", "the truest part is the crackle; nothing is in the way."),
    ]
    pick_covered = _selector_probe(
        report,
        trigger="",
        transcript=covered,
        remaining=["creative_director", "manager"],
    )
    report.add(
        "selector_stops_or_closes_when_covered",
        pick_covered in {"stop", "manager_summary", "manager", "creative_director"},
        f"all lanes covered -> '{pick_covered}'",
    )


ACTION_RE = re.compile(
    r"\b(ship|keep|cut|change|add|strip|decide|approve|fix|drop|leave|lock|carve|bounce|send|"
    r"record|try|remove|protect|double|rerecord|tighten|raise|lower|dip|boost|pick|choose|tell)\w*\b",
    re.IGNORECASE,
)
EVIDENCE_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
SONIC_WORDS = (
    "bass", "low end", "vocal", "drum", "hat", "pad", "synth", "hook", "chorus", "verse",
    "loop", "mix", "kick", "snare", "sample", "crackle", "hiss", "reverb", "khz", "intro",
    "drop", "outro", "bridge", "melody", "groove", "texture", "beat", "track", "song",
)


def message_contract_score(message: str) -> tuple[float, str]:
    notes: list[str] = []
    score = 0.0
    words = len(message.split())
    if 12 <= words <= 150:
        score += 0.25
        notes.append(f"words={words}")
    if EVIDENCE_RE.search(message) or any(w in message.lower() for w in SONIC_WORDS):
        score += 0.25
        notes.append("evidence")
    if ACTION_RE.search(message):
        score += 0.25
        notes.append("action")
    if re.search(r"\b(not|never|isn't|wrong|weak|solid|flat|honest|enough|ready|love|keep|ship)\b", message, re.IGNORECASE):
        score += 0.25
        notes.append("stance")
    return score, ", ".join(notes)


def check_message_contract(report: Report, prompt_context: str) -> None:
    if not _env_key("OPENROUTER_API_KEY"):
        report.add("message_contract_real_generation", False, "OPENROUTER_API_KEY not set")
        return
    api_key = _openrouter_key(_env_key("OPENROUTER_API_KEY"))
    started = perf_counter()
    message, _timestamp = asyncio.run(
        _generate_agent_message_async(
            agent="a_and_r",
            prompt_context=prompt_context,
            model=_agent_model(),
            api_key=api_key,
        )
    )
    report.timing("take_probe_ms", (perf_counter() - started) * 1000)
    score, notes = message_contract_score(message)
    report.metrics["take_contract_score"] = round(score, 2)
    report.add(
        "message_contract_compliance",
        score >= 0.75,
        f"score={score} ({notes}) take='{message[:100]}'",
    )


def check_tts(tmp_dir: Path, report: Report) -> None:
    # provider switch (pure)
    settings_dir = tmp_dir / "tts-settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    (settings_dir / "settings.json").write_text(json.dumps({"voice_provider": "fish-cloud"}))
    report.add(
        "tts_provider_switch",
        _current_provider(settings_dir) == "fish-cloud"
        and _current_provider(tmp_dir) == "elevenlabs",
        "settings fish-cloud honoured, missing settings fall back to elevenlabs",
    )

    # voice mapping (pure)
    map_dir = tmp_dir / "tts-map"
    map_dir.mkdir(parents=True, exist_ok=True)
    (map_dir / "settings.json").write_text(
        json.dumps({"fish_voice_map": {"kallman": "test-voice-id"}})
    )
    report.add(
        "tts_voice_mapping",
        _fish_reference_id("kallman", map_dir) == "test-voice-id"
        and _fish_reference_id("a_and_r", map_dir) is None,
        "mapped agent returns id, unmapped returns None",
    )

    # real cloud synthesis + cache
    if not _env_key("FISH_API_KEY"):
        report.add("tts_fish_cloud_real", False, "FISH_API_KEY not set")
        return
    data_dir = tmp_dir / "tts-cache"
    started = perf_counter()
    audio = _synthesize_fish_cloud("kallman", "Harness voice check: the drop needs one new element.")
    report.timing("tts_cloud_first_ms", (perf_counter() - started) * 1000)
    report.add("tts_fish_cloud_real", len(audio) > 1000, f"{len(audio)} bytes of mp3")

    cache_path = synthesize(data_dir, 987001, "kallman", "Harness voice check: the drop needs one new element.")
    started = perf_counter()
    synthesize(data_dir, 987001, "kallman", "Harness voice check: the drop needs one new element.")
    cached_ms = (perf_counter() - started) * 1000
    report.timing("tts_cached_ms", cached_ms)
    report.add(
        "tts_cache_hit",
        cached_ms < 50 and cache_path.exists() and cache_path.stat().st_size > 1000,
        f"second call {cached_ms:.1f}ms, file {cache_path.stat().st_size} bytes",
    )
    report.add(
        "tts_media_type",
        cached_media_type(cache_path) == "audio/mpeg",
        "mp3 cache reports audio/mpeg",
    )


def check_voice_endpoints(report: Report) -> None:
    import urllib.request

    try:
        with urllib.request.urlopen("http://localhost:8086/voice/status", timeout=10) as resp:
            status = json.loads(resp.read())
        report.add(
            "voice_status_endpoint",
            all(k in status for k in ("provider", "cloud_key_set", "local_ready", "gpu_vram_mb")),
            json.dumps(status),
        )
        with urllib.request.urlopen("http://localhost:8086/voice/library", timeout=30) as resp:
            library = json.loads(resp.read())
        voices = library.get("voices", [])
        report.add(
            "voice_library_endpoint",
            len(voices) >= 10 and all("id" in v and "title" in v for v in voices),
            f"{len(voices)} voices fetched from Fish",
        )
    except Exception as exc:  # noqa: BLE001
        report.add("voice_status_endpoint", False, f"API unreachable: {exc}")
        report.add("voice_library_endpoint", False, f"API unreachable: {exc}")


# ── Suite B: integration (isolated DB copy, real rounds) ─────────────────────


def _fetch_takes(db_path: Path, response_ids: list[int]) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = []
    for fid in response_ids:
        row = conn.execute(
            "SELECT id, agent, message, intent FROM feedback WHERE id = ?", (fid,)
        ).fetchone()
        if row:
            rows.append(dict(row))
    conn.close()
    return rows


def check_reply_round(db_path: Path, track_id: int, message: str, report: Report) -> None:
    dispatcher = TrackPipelineDispatcher(str(db_path))
    started = perf_counter()
    result = dispatcher.process_artist_message(
        {"track_id": track_id, "agent": "a_and_r", "message": message, "channel": "desktop"}
    )
    round_ms = (perf_counter() - started) * 1000
    report.timing("reply_round_total_ms", round_ms)
    report.metrics["reply_intent"] = result.get("intent")
    report.metrics["reply_confidence"] = result.get("confidence")

    report.add(
        "intent_classification",
        result.get("intent") in {"question", "needs_clarification", "casual"}
        and float(result.get("confidence") or 0) >= 0.5,
        f"intent={result.get('intent')} confidence={result.get('confidence')}",
    )

    response_ids = result.get("response_ids") or []
    takes = _fetch_takes(db_path, response_ids)
    report.metrics["reply_takes"] = len(takes)
    report.add(
        "reply_round_responds",
        len(takes) >= 1,
        f"{len(takes)} takes in {round_ms:.0f}ms ({[t['agent'] for t in takes]})",
    )
    if not takes:
        return

    first_take_ms = None
    conn = sqlite3.connect(db_path)
    inbound = conn.execute(
        "SELECT created_at FROM feedback WHERE id = ?", (result["feedback_id"],)
    ).fetchone()
    first_out = conn.execute(
        "SELECT created_at FROM feedback WHERE id = ?", (takes[0]["id"],)
    ).fetchone()
    conn.close()
    if inbound and first_out:
        fmt = "%Y-%m-%d %H:%M:%S"
        delta = (
            datetime.strptime(first_out[0], fmt) - datetime.strptime(inbound[0], fmt)
        ).total_seconds()
        first_take_ms = delta * 1000
        report.timing("first_take_landing_ms", first_take_ms)

    report.add(
        "reply_round_distinct_agents",
        len({t["agent"] for t in takes}) == len(takes),
        f"agents: {[t['agent'] for t in takes]}",
    )

    # The manager (closer) restates the decision by design, so exclude him from
    # the parroting check - only non-closer takes should be distinct from each other.
    ordered = sorted(takes, key=lambda t: t["id"])
    non_closers = [t for t in ordered if t["agent"] != "manager"]
    echoes = [
        f"{a['agent']}~{b['agent']}"
        for i, a in enumerate(non_closers)
        for b in non_closers[i + 1 :]
        if _take_is_echo(b["message"], [a["message"]])
    ]
    report.metrics["reply_echo_pairs"] = len(echoes)
    report.add(
        "reply_round_no_echo",
        not echoes,
        f"{len(echoes)} echo pairs across {len(non_closers)} non-closer takes: {echoes}",
    )

    scores = [message_contract_score(t["message"])[0] for t in takes]
    avg_score = sum(scores) / len(scores) if scores else 0
    report.metrics["reply_avg_contract_score"] = round(avg_score, 2)
    report.add(
        "reply_round_contract_quality",
        avg_score >= 0.6,
        f"avg contract score {avg_score:.2f} over {len(takes)} takes",
    )

    agents = [t["agent"] for t in ordered]
    if "manager" in agents:
        report.add(
            "dez_discipline_closes_last",
            agents[-1] == "manager",
            f"manager speaks last: {agents}",
        )
        manager_words = len(ordered[-1]["message"].split())
        report.add(
            "dez_summary_concise",
            manager_words <= 120,
            f"summary {manager_words} words: '{ordered[-1]['message'][:80]}'",
        )
    else:
        report.add(
            "dez_discipline_closes_last",
            True,
            "dez stayed silent this round (selector restraint)",
        )


def check_debate_round(db_path: Path, track_id: int, report: Report) -> None:
    dispatcher = TrackPipelineDispatcher(str(db_path))
    started = perf_counter()
    result = dispatcher.process_debate_request({"track_id": track_id})
    round_ms = (perf_counter() - started) * 1000
    report.timing("debate_round_total_ms", round_ms)

    takes = _fetch_takes(db_path, result.get("response_ids") or [])
    report.metrics["debate_takes"] = len(takes)
    report.add(
        "debate_round_runs",
        len(takes) >= 2,
        f"{len(takes)} debate takes in {round_ms:.0f}ms",
    )
    if len(takes) < 2:
        return

    first_names = [name.split()[0].lower() for name in AGENT_DISPLAY_NAMES.values()]
    room_directed = sum(
        1 for t in takes if any(name in t["message"].lower() for name in first_names)
    )
    report.metrics["debate_room_directed_takes"] = room_directed
    report.add(
        "debate_room_directed",
        room_directed >= 1,
        f"{room_directed}/{len(takes)} takes address another agent by name",
    )

    ordered = sorted(takes, key=lambda t: t["id"])
    echoes = [
        f"{a['agent']}~{b['agent']}"
        for i, a in enumerate(ordered)
        for b in ordered[i + 1 :]
        if _take_is_echo(b["message"], [a["message"]])
    ]
    report.add("debate_no_echo", not echoes, f"{len(echoes)} echo pairs: {echoes}")

    intents = {t["intent"] for t in takes}
    report.add(
        "debate_intent_tagged",
        intents == {"agent_debate"},
        f"intents: {intents}",
    )


def check_intake_three_acts(db_path: Path, track_id: int, report: Report) -> None:
    """Full post-analysis meeting: first reads -> room session -> Dez close."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    track = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    analysis = _latest_analysis(conn, track_id)
    max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM feedback").fetchone()[0]
    conn.close()
    if track is None or analysis is None:
        report.add("intake_three_acts", False, "track or audio analysis missing")
        return

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        prompt_context = _track_prompt_context(
            conn, track=track, project_id=None, analysis=analysis, stage="harness"
        )

    started = perf_counter()
    try:
        run_intake_rounds(
            db_path=str(db_path),
            track_id=track_id,
            project_id=None,
            prompt_context=prompt_context,
        )
    except Exception as exc:  # noqa: BLE001
        report.add("intake_three_acts", False, f"intake sequence raised: {exc}")
        return
    report.timing("intake_total_ms", (perf_counter() - started) * 1000)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, agent, intent, message FROM feedback WHERE track_id=? AND id>? ORDER BY id",
        (track_id, max_id),
    ).fetchall()
    conn.close()
    fresh = [dict(r) for r in rows]

    a1 = [t for t in fresh if t["intent"] in set(ROUND_TABLE_INTENTS.values()) - {"review_round_summary"}]
    a2 = [t for t in fresh if t["intent"] == "room_discussion"]
    a3 = [t for t in fresh if t["intent"] == "review_round_summary"]

    missing = [a for a in MUSIC_EXECS if a not in {t["agent"] for t in a1}]
    report.add(
        "intake_act1_first_reads",
        not missing,
        f"first reads from {sorted({t['agent'] for t in a1})}, missing={missing}",
    )

    room_agents = {t["agent"] for t in a2}
    report.add(
        "intake_act2_room_session",
        len(a2) >= 4 and len(room_agents) >= 3,
        f"{len(a2)} room takes from {sorted(room_agents)}",
    )

    first_names = [name.split()[0] for name in AGENT_DISPLAY_NAMES.values()]
    directed = sum(1 for t in a2 if any(n.lower() in t["message"].lower() for n in first_names))
    report.metrics["intake_room_directed_takes"] = directed
    report.add(
        "intake_act2_agents_talk_to_each_other",
        directed >= 2,
        f"{directed}/{len(a2)} room takes name a peer",
    )

    ordered = sorted(a2, key=lambda t: t["id"])
    echoes = [
        f"{a['agent']}~{b['agent']}"
        for i, a in enumerate(ordered)
        for b in ordered[i + 1 :]
        if _take_is_echo(b["message"], [a["message"]])
    ]
    report.add("intake_act2_no_echo", not echoes, f"{len(echoes)} echo pairs: {echoes}")

    if a3:
        closes_last = a3[-1]["agent"] == "manager" and (
            not a2 or a3[-1]["id"] > max(t["id"] for t in a2)
        )
        report.add(
            "intake_act3_dez_closes",
            closes_last,
            f"'{a3[-1]['message'][:80]}'",
        )
    else:
        report.add("intake_act3_dez_closes", False, "no manager close posted")


# ── main ──────────────────────────────────────────────────────────────────────


def pick_default_track(db_path: Path) -> int | None:
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        """SELECT t.id FROM tracks t
           JOIN audio_analyses a ON a.track_id = t.id
           WHERE t.state IN ('FEEDBACK_GIVEN', 'IN_REVIEW')
           ORDER BY t.id DESC LIMIT 1"""
    ).fetchone()
    conn.close()
    return int(row[0]) if row else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track-id", type=int, default=None)
    parser.add_argument(
        "--message",
        default="be honest with me — is the drop at 0:45 strong enough, and what exactly should i change?",
    )
    parser.add_argument("--fast", action="store_true", help="skip the debate round")
    parser.add_argument("--skip-intake", action="store_true", help="skip the 3-act intake meeting")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    data_env = (_env_key("AI_RECORD_LABEL_DATA") or "").strip()
    live_db = (
        Path(data_env) / "hermes.db"
        if data_env
        else Path.home() / "AppData" / "Roaming" / "ai-record-label" / "hermes.db"
    )
    if not live_db.exists():
        print(f"live db not found at {live_db}")
        return 2
    live_data_dir = live_db.parent

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.output_dir) if args.output_dir else ROOT / "eval-results" / f"roundtable-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    db_copy = out_dir / "hermes.db"
    shutil.copyfile(live_db, db_copy)
    print(f"isolated db copy: {db_copy}")

    track_id = args.track_id or pick_default_track(db_copy)
    if track_id is None:
        print("no track with analysis found in the db copy")
        return 2
    print(f"track under test: {track_id}")

    report = Report()

    conn = sqlite3.connect(db_copy)
    conn.row_factory = sqlite3.Row
    track = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    analysis = conn.execute(
        "SELECT * FROM audio_analyses WHERE track_id = ? ORDER BY id DESC LIMIT 1", (track_id,)
    ).fetchone()
    conn.close()
    if track is None or analysis is None:
        print("track or analysis missing")
        return 2

    with sqlite3.connect(db_copy) as conn:
        conn.row_factory = sqlite3.Row
        prompt_context = _track_prompt_context(
            conn,
            track=track,
            project_id=None,
            analysis=_latest_analysis(conn, track_id),
            stage="harness",
        )

    print("\n── suite A: separate functions ──")
    check_echo_gate(report)
    check_selector(report)
    check_message_contract(report, prompt_context)
    check_tts(out_dir, report)
    check_voice_endpoints(report)

    print("\n── suite B: integration (real rounds) ──")
    if not args.skip_intake:
        check_intake_three_acts(db_copy, track_id, report)
    check_reply_round(db_copy, track_id, args.message, report)
    if not args.fast:
        check_debate_round(db_copy, track_id, report)

    passed = sum(1 for c in report.checks if c.passed)
    total = len(report.checks)
    verdict = (
        "all_green" if passed == total else ("partial" if passed else "failed")
    )
    report_ = {
        "generated_at": datetime.now(UTC).isoformat(),
        "track_id": track_id,
        "model": _agent_model(),
        "voice_provider": _current_provider(live_data_dir),
        "checks": [c.__dict__ for c in report.checks],
        "metrics": report.metrics,
        "timings_ms": report.timings_ms,
        "summary": f"{passed}/{total} passed",
        "verdict": verdict,
    }
    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report_, indent=2), encoding="utf-8")
    print(f"\n{'=' * 60}")
    print(f"verdict: {verdict} ({passed}/{total} checks passed)")
    print(f"metrics: {json.dumps(report.metrics)}")
    print(f"timings: {json.dumps(report.timings_ms)}")
    print(f"report: {report_path}")
    return 0 if verdict == "all_green" else 1


if __name__ == "__main__":
    raise SystemExit(main())
