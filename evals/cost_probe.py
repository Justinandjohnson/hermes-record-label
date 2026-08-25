"""Cost probe: run one REAL production call per candidate model (analysis,
segments, take generation) while capturing actual token usage from the
OpenRouter responses, then compute $ per call and $ per full intake run.

Merges the cost matrix into the latest model-battery report.json.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from audio_analysis.analyzer import analyze  # noqa: E402
from audio_analysis.gemini_client import _openrouter_key  # noqa: E402
from audio_analysis.segment_analyzer import analyze_segments  # noqa: E402
from coordination.dispatcher import (  # noqa: E402
    _generate_agent_message_async,
    _latest_analysis,
    _track_prompt_context,
)

AUDIO_MODELS = [
    "google/gemini-3.1-pro-preview",
    "google/gemini-3.7-flash",
    "google/gemini-3.1-flash-lite",
    "google/gemini-2.5-flash",
]
TEXT_MODELS = [
    "qwen/qwen3.8-27b",
    "qwen/qwen3.7-flash",
    "qwen/qwen3.6-flash",
    "google/gemini-2.5-flash",
]
TAKES_PER_INTAKE = 12


def _load_prices() -> dict[str, tuple[float, float]]:
    api_key = _openrouter_key(os.environ.get("OPENROUTER_API_KEY"))
    response = httpx.get(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    response.raise_for_status()
    wanted = set(AUDIO_MODELS) | set(TEXT_MODELS)
    prices = {}
    for m in response.json().get("data", []):
        if m["id"] in wanted:
            p = m.get("pricing") or {}
            prices[m["id"]] = (
                float(p.get("prompt", 0)) * 1_000_000,
                float(p.get("completion", 0)) * 1_000_000,
            )
    return prices


def _install_capture() -> list[dict]:
    captured: list[dict] = []
    original = httpx.AsyncClient.post

    async def patched(self, url, *args, **kwargs):
        response = await original(self, url, *args, **kwargs)
        try:
            if "openrouter.ai" in str(url):
                body = response.json()
                payload = kwargs.get("json") or {}
                usage = body.get("usage") or {}
                captured.append(
                    {
                        "model": payload.get("model"),
                        "prompt_tokens": usage.get("prompt_tokens") or 0,
                        "completion_tokens": usage.get("completion_tokens") or 0,
                        "api_cost": body.get("cost"),
                    }
                )
        except Exception:
            pass
        return response

    httpx.AsyncClient.post = patched
    return captured


def _cost_of(calls: list[dict], prices: dict[str, tuple[float, float]]) -> tuple[float, int, int]:
    total = 0.0
    prompt_tokens = 0
    completion_tokens = 0
    for call in calls:
        in_price, out_price = prices.get(call["model"], (0.0, 0.0))
        prompt_tokens += call["prompt_tokens"]
        completion_tokens += call["completion_tokens"]
        if isinstance(call["api_cost"], (int, float)):
            total += float(call["api_cost"])
        else:
            total += call["prompt_tokens"] * in_price / 1_000_000 + call["completion_tokens"] * out_price / 1_000_000
    return total, prompt_tokens, completion_tokens


def _live_prompt_context(track_id: int) -> str:
    data_dir = Path(os.environ.get("AI_RECORD_LABEL_DATA", str(Path(os.environ["APPDATA"]) / "ai-record-label")))
    conn = sqlite3.connect(f"file:{data_dir / 'hermes.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        track = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        analysis = _latest_analysis(conn, track_id)
        if track is None or analysis is None:
            raise SystemExit(f"track {track_id} missing or unanalyzed")
        return _track_prompt_context(
            conn, track=track, project_id=None, analysis=analysis,
            stage="post_analysis_review_round",
        )
    finally:
        conn.close()


def _call_with_retry(fn, *args, **kwargs):
    """One retry: transient provider hiccups (empty content) happen."""
    try:
        return fn(*args, **kwargs)
    except Exception:
        return fn(*args, **kwargs)


def main() -> int:
    battery_runs = sorted((ROOT / "eval-results").iterdir(), key=lambda p: p.name)
    battery_runs = [p for p in battery_runs if p.name.startswith("model-battery")]
    run_dir = battery_runs[-1]
    work = run_dir / "audio-work"
    db_path = work / "battery.db"
    audio_file = max(work.glob("*.mp3"))
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))

    prices = _load_prices()
    captured = _install_capture()
    api_key = _openrouter_key(os.environ.get("OPENROUTER_API_KEY"))
    prompt_context = _live_prompt_context(2)

    cost: dict[str, dict] = dict(report.get("cost") or {})

    def _save() -> None:
        report["takes_per_intake"] = TAKES_PER_INTAKE
        report["cost"] = cost
        (run_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    for model in AUDIO_MODELS:
        if "analysis" in cost.get(model, {}):
            print(f"{model:35s} (resumed, skipping)")
            continue
        try:
            start = len(captured)
            _call_with_retry(lambda: analyze(str(audio_file), str(db_path), track_id=1, model=model))
            analysis_calls = captured[start:]
            start = len(captured)
            _call_with_retry(lambda: asyncio.run(analyze_segments(str(audio_file), str(db_path), 1, model=model)))
            segment_calls = captured[start:]
            a_cost, a_in, a_out = _cost_of(analysis_calls, prices)
            s_cost, s_in, s_out = _cost_of(segment_calls, prices)
            cost[model] = {
                "price_in_per_m": prices.get(model, (None, None))[0],
                "price_out_per_m": prices.get(model, (None, None))[1],
                "analysis": {"cost": round(a_cost, 4), "prompt_tokens": a_in, "completion_tokens": a_out, "calls": len(analysis_calls)},
                "segments": {"cost": round(s_cost, 4), "prompt_tokens": s_in, "completion_tokens": s_out, "calls": len(segment_calls)},
            }
            print(f"{model:35s} analysis=${a_cost:.4f} ({len(analysis_calls)} calls) segments=${s_cost:.4f} ({len(segment_calls)} calls)")
        except Exception as exc:
            cost[model] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            print(f"{model:35s} FAILED: {type(exc).__name__}: {str(exc)[:120]}")
        _save()

    for model in TEXT_MODELS:
        if "take" in cost.get(model, {}):
            print(f"{model:35s} (resumed, skipping)")
            continue
        try:
            start = len(captured)
            _call_with_retry(
                lambda: asyncio.run(
                    _generate_agent_message_async(
                        agent="a_and_r", prompt_context=prompt_context, model=model,
                        api_key=api_key, audience="artist",
                    )
                )
            )
            take_calls = [c for c in captured[start:] if c["completion_tokens"] > 20]
            t_cost, t_in, t_out = _cost_of(take_calls, prices)
            cost[model] = {
                **cost.get(model, {}),
                "price_in_per_m": prices.get(model, (None, None))[0],
                "price_out_per_m": prices.get(model, (None, None))[1],
                "take": {"cost": round(t_cost, 4), "prompt_tokens": t_in, "completion_tokens": t_out, "calls": len(take_calls)},
            }
            print(f"{model:35s} take=${t_cost:.4f} (in={t_in} out={t_out})")
        except Exception as exc:
            cost[model] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            print(f"{model:35s} FAILED: {type(exc).__name__}: {str(exc)[:120]}")
        _save()

    for model, entry in cost.items():
        if "analysis" in entry and "take" in entry:
            per_intake = (
                entry["analysis"]["cost"]
                + entry["segments"]["cost"]
                + TAKES_PER_INTAKE * entry["take"]["cost"]
            )
            entry["est_cost_per_intake"] = round(per_intake, 4)
    _save()

    print("\n=== COST MATRIX (measured, $ per full intake run = 1 analysis + 1 segments + 12 takes) ===")
    print(f"{'model':35s} {'in$/M':>7s} {'out$/M':>7s} {'analysis':>9s} {'segs':>9s} {'take':>8s} {'/intake':>9s}")
    for model, entry in cost.items():
        a = f"${entry['analysis']['cost']:.3f}" if "analysis" in entry else "  -  "
        s = f"${entry['segments']['cost']:.3f}" if "segments" in entry else "  -  "
        t = f"${entry['take']['cost']:.4f}" if "take" in entry else "  -  "
        per = f"${entry['est_cost_per_intake']:.3f}" if "est_cost_per_intake" in entry else "  -  "
        print(f"{model:35s} {str(entry['price_in_per_m']):>7s} {str(entry['price_out_per_m']):>7s} {a:>9s} {s:>9s} {t:>8s} {per:>9s}")
    print(f"\nupdated: {run_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
