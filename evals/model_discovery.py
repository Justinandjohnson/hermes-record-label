"""Discover OpenRouter model candidates for the latency battery.

Reads the live model catalog (one API call) and prints:
  - audio-capable models (input_modalities includes 'audio') with pricing
  - fast/cheap chat models from the families in use
No writes, no side effects.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from audio_analysis.gemini_client import _openrouter_key


def main() -> int:
    key = _openrouter_key(os.environ.get("OPENROUTER_API_KEY"))
    resp = httpx.get(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    models = resp.json().get("data", [])
    print(f"total models: {len(models)}")

    print("\n=== AUDIO-INPUT models ===")
    for m in models:
        arch = m.get("architecture") or {}
        if "audio" in (arch.get("input_modalities") or []):
            pricing = m.get("pricing") or {}
            print(
                f"{m['id']:55s} in=${float(pricing.get('prompt', 0)) * 1_000_000:.2f}/M "
                f"out=${float(pricing.get('completion', 0)) * 1_000_000:.2f}/M "
                f"ctx={m.get('context_length')}"
            )

    families = ("qwen", "gemini", "claude", "deepseek", "mistral")
    print("\n=== FAST CHAT candidates (low prompt price, big context) ===")
    rows = []
    for m in models:
        pricing = m.get("pricing") or {}
        try:
            prompt_price = float(pricing.get("prompt", 1))
        except (TypeError, ValueError):
            continue
        arch = m.get("architecture") or {}
        if "text" not in (arch.get("input_modalities") or []):
            continue
        if prompt_price <= 0.000005 and (m.get("context_length") or 0) >= 32_000:
            if any(f in m["id"].lower() for f in families):
                rows.append((prompt_price, m["id"], m.get("context_length")))
    for price, mid, ctx in sorted(rows)[:60]:
        print(f"{mid:55s} ${price * 1_000_000:.2f}/M ctx={ctx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
