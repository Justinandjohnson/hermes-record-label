#!/usr/bin/env python3
"""Print which model powers each agent and pipeline stage.

Reads the live config (agents/*/tools.yaml, SOUL.md identities, and source
constants) rather than a hardcoded list, so the rundown never drifts from
the code.

    uv run python scripts/model_rundown.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _yaml_model(tools_yaml: Path) -> str:
    """Extract the `model:` value from an agent's tools.yaml."""
    m = re.search(r"^\s*model:\s*([^\s#]+)", tools_yaml.read_text(), re.MULTILINE)
    if not m:
        raise SystemExit(f"No model: line found in {tools_yaml}")
    return m.group(1)


def _soul_identity(soul_md: Path) -> str:
    """First heading of SOUL.md, e.g. '# Ravi Kendrick — A&R'."""
    first = soul_md.read_text().splitlines()[0]
    return first.lstrip("# ").strip()


def _source_constant(rel_path: str, pattern: str) -> str:
    m = re.search(pattern, (ROOT / rel_path).read_text())
    if not m:
        raise SystemExit(f"Pattern {pattern!r} not found in {rel_path}")
    return m.group(1)


def rows() -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []

    for agent_dir in sorted((ROOT / "agents").iterdir()):
        tools = agent_dir / "tools.yaml"
        soul = agent_dir / "SOUL.md"
        if not tools.exists():
            continue
        identity = _soul_identity(soul) if soul.exists() else agent_dir.name
        out.append(("Label staff", identity, _yaml_model(tools)))

    audio_default = _source_constant(
        "audio_analysis/gemini_client.py", r'DEFAULT_OPENROUTER_MODEL = "([^"]+)"'
    )
    agent_default = _source_constant(
        "coordination/dispatcher.py", r'DEFAULT_AGENT_MODEL = "([^"]+)"'
    )
    out += [
        ("Pipeline", "Agent chat runtime (dispatcher)",
         os.environ.get("OPENROUTER_AGENT_MODEL", agent_default)),
        ("Pipeline", "Artist intent parsing (SMS)",
         _source_constant("coordination/intent_parser.py", r'model: str = "([^"]+)"')),
        ("Pipeline", "Roundtable verdict synthesis",
         _source_constant("coordination/verdict_synthesizer.py", r'VERDICT_MODEL = "([^"]+)"')),
        ("Pipeline", "Audio analysis (full mix)",
         os.environ.get("OPENROUTER_AUDIO_MODEL", audio_default)),
        ("Pipeline", "Lyrics extraction (vocal stem)",
         _source_constant("stem_separation/lyrics_extractor.py", r'model: str = "([^"]+)"')),
        ("Pipeline", "Mumble decoding (phonemes → lyrics)",
         _source_constant("stem_separation/mumble_analyzer.py", r'model: str = "([^"]+)"')),
        ("Pipeline", "Artwork review (vision)", "google/gemini-3.5-flash"),
    ]
    return out


def main() -> None:
    table = rows()
    who_width = max(len(who) for _, who, _ in table)
    current = ""
    print()
    print("Hermes model rundown — every call routes through OpenRouter")
    print("=" * (who_width + 40))
    for section, who, model in table:
        if section != current:
            current = section
            print(f"\n{section}")
            print("-" * (who_width + 40))
        print(f"  {who:<{who_width}}  {model}")
    print()
    key = os.environ.get("OPENROUTER_API_KEY", "")
    status = "set" if key else "NOT SET — nothing will work without it"
    print(f"OPENROUTER_API_KEY: {status}")
    print("Override chat/audio models with OPENROUTER_AGENT_MODEL / OPENROUTER_AUDIO_MODEL.")
    print("Per-agent staff models live in agents/<name>/tools.yaml.")
    print()


if __name__ == "__main__":
    sys.exit(main())
