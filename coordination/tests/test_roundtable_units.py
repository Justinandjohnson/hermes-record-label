"""Hermetic unit tests for the roundtable + voice-provider building blocks.

These cover each function SEPARATELY (no network): echo gate, selector
validation, round context assembly, TTS provider resolution, voice mapping,
and the synthesis cache. Real-pipeline behaviour is measured by
evals/roundtable_harness.py.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from coordination.dispatcher import (
    _round_context,
    _select_next_speaker_async,
    _take_is_echo,
)


# ── echo gate ─────────────────────────────────────────────────────────────────


class TestEchoGate:
    ORIGINAL = "the low end is muddy at 0:45, carve the 1-2kHz range and ship it"

    def test_catches_near_identical(self):
        assert _take_is_echo(
            "the low end is muddy at 0:45, carve the 1-2kHz range and ship it", [self.ORIGINAL]
        )

    def test_catches_paraphrase(self):
        assert _take_is_echo(
            "bass frequencies get muddy around 0:45 — notch 1-2khz then ship",
            [self.ORIGINAL],
        )

    def test_passes_distinct_lane(self):
        assert not _take_is_echo(
            "janick is right about world-building, but the hook needs a second layer",
            [self.ORIGINAL],
        )

    def test_empty_inputs_safe(self):
        assert not _take_is_echo("", [self.ORIGINAL])
        assert not _take_is_echo(self.ORIGINAL, [])
        assert not _take_is_echo("", [])

    def test_multiple_priors(self):
        priors = [
            "the hook declares itself immediately, i would run it back",
            "protect the vinyl crackle, that texture is the identity",
        ]
        assert not _take_is_echo("carve 1-2khz so the vocal sample at 1:00 cuts through", priors)
        assert _take_is_echo("that crackle texture is the identity, protect it", priors)


# ── round context ─────────────────────────────────────────────────────────────


class TestRoundContext:
    def test_appends_trigger_and_transcript(self):
        base = "{}"
        out = _round_context(base, "is the drop strong enough?", [("kallman", "it has it")])
        assert "THE ARTIST JUST SAID" in out
        assert "is the drop strong enough?" in out
        assert "Craig Kallman: it has it" in out
        assert "TAKES ALREADY GIVEN" in out

    def test_base_context_untouched_when_empty(self):
        assert _round_context("CTX", "", []) == "CTX"


# ── selector validation ───────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload, is_error=False):
        self._payload = payload
        self.is_error = is_error

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload, is_error=False):
        self._payload = payload
        self._is_error = is_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        return _FakeResponse(
            {"choices": [{"message": {"content": json.dumps(self._payload)}}]},
            self._is_error,
        )


@pytest.mark.asyncio
async def test_selector_returns_valid_pick(monkeypatch):
    import coordination.dispatcher as dispatcher

    monkeypatch.setattr(
        dispatcher.httpx, "AsyncClient", lambda **kw: _FakeClient({"next": "kallman"})
    )
    pick = await _select_next_speaker_async(
        remaining=["kallman", "a_and_r"],
        transcript=[],
        trigger_text="",
        stage_label="test",
        turns_left=3,
        allow_manager_summary=True,
        model="test-model",
        api_key="test",
    )
    assert pick == "kallman"


@pytest.mark.asyncio
async def test_selector_rejects_unknown_agent(monkeypatch):
    import coordination.dispatcher as dispatcher

    monkeypatch.setattr(
        dispatcher.httpx, "AsyncClient", lambda **kw: _FakeClient({"next": "bandcamp"})
    )
    pick = await _select_next_speaker_async(
        remaining=["kallman", "a_and_r"],
        transcript=[],
        trigger_text="",
        stage_label="test",
        turns_left=3,
        allow_manager_summary=True,
        model="test-model",
        api_key="test",
    )
    assert pick == "stop"


@pytest.mark.asyncio
async def test_selector_manager_summary_requires_permission(monkeypatch):
    import coordination.dispatcher as dispatcher

    monkeypatch.setattr(
        dispatcher.httpx, "AsyncClient", lambda **kw: _FakeClient({"next": "manager_summary"})
    )
    allowed = await _select_next_speaker_async(
        remaining=["manager"],
        transcript=[("a_and_r", "carve 1-2khz")],
        trigger_text="",
        stage_label="test",
        turns_left=2,
        allow_manager_summary=True,
        model="test-model",
        api_key="test",
    )
    denied = await _select_next_speaker_async(
        remaining=["manager"],
        transcript=[("a_and_r", "carve 1-2khz")],
        trigger_text="",
        stage_label="test",
        turns_left=2,
        allow_manager_summary=False,
        model="test-model",
        api_key="test",
    )
    assert allowed == "manager_summary"
    assert denied == "stop"


@pytest.mark.asyncio
async def test_selector_malformed_json_stops(monkeypatch):
    import coordination.dispatcher as dispatcher

    monkeypatch.setattr(
        dispatcher.httpx,
        "AsyncClient",
        lambda **kw: _FakeClient({"choices": [{"message": {"content": "not json"}}]}),
    )
    pick = await _select_next_speaker_async(
        remaining=["kallman"],
        transcript=[],
        trigger_text="",
        stage_label="test",
        turns_left=1,
        allow_manager_summary=True,
        model="test-model",
        api_key="test",
    )
    assert pick == "stop"


# ── TTS provider resolution ───────────────────────────────────────────────────


class TestTtsProvider:
    def _dir_with(self, tmp_path: Path, settings: dict) -> Path:
        d = tmp_path / "data"
        d.mkdir()
        (d / "settings.json").write_text(json.dumps(settings))
        return d

    def test_provider_from_settings(self, tmp_path):
        from audio_analysis.tts import _current_provider

        d = self._dir_with(tmp_path, {"voice_provider": "fish-cloud"})
        assert _current_provider(d) == "fish-cloud"

    def test_provider_default_without_file(self, tmp_path):
        from audio_analysis.tts import _current_provider

        assert _current_provider(tmp_path) == "elevenlabs"

    def test_provider_invalid_falls_back(self, tmp_path):
        from audio_analysis.tts import _current_provider

        d = self._dir_with(tmp_path, {"voice_provider": "hal-9000"})
        assert _current_provider(d) == "elevenlabs"

    def test_voice_mapping_settings_first(self, tmp_path, monkeypatch):
        import audio_analysis.tts as tts

        d = self._dir_with(tmp_path, {"fish_voice_map": {"kallman": "from-settings"}})
        monkeypatch.setattr(tts, "AGENT_FISH_VOICE_IDS", {"kallman": "from-env"})
        assert tts._fish_reference_id("kallman", d) == "from-settings"
        assert tts._fish_reference_id("a_and_r", d) is None

    def test_voice_mapping_env_fallback(self, tmp_path, monkeypatch):
        import audio_analysis.tts as tts

        d = self._dir_with(tmp_path, {})
        monkeypatch.setattr(tts, "AGENT_FISH_VOICE_IDS", {"kallman": "from-env"})
        assert tts._fish_reference_id("kallman", d) == "from-env"

    def test_local_reference_uses_hyphens(self, tmp_path, monkeypatch):
        import audio_analysis.tts as tts

        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured["payload"] = json

            class R:
                def raise_for_status(self):
                    return None

                content = b"RIFFfake-wav-bytes"

            return R()

        monkeypatch.setattr(tts.httpx, "post", fake_post)
        audio = tts._synthesize_fish_local("creative_director", "test")
        assert audio.startswith(b"RIFF")
        assert captured["payload"]["reference_id"] == "creative-director"


# ── synthesis cache ───────────────────────────────────────────────────────────


class TestSynthesisCache:
    def test_cache_prevents_regeneration(self, tmp_path, monkeypatch):
        import audio_analysis.tts as tts

        calls = {"n": 0}

        def fake_cloud(agent, text, data_dir=None):
            calls["n"] += 1
            return b"mp3-bytes-" + str(calls["n"]).encode()

        monkeypatch.setattr(tts, "_synthesize_fish_cloud", fake_cloud)
        monkeypatch.setattr(tts, "_current_provider", lambda data_dir: "fish-cloud")

        first = tts.synthesize(tmp_path, 4242, "kallman", "line one")
        second = tts.synthesize(tmp_path, 4242, "kallman", "line one")
        other = tts.synthesize(tmp_path, 4243, "kallman", "line two")

        assert calls["n"] == 2  # first generation + the different message id
        assert first.read_bytes() == second.read_bytes()
        assert other.read_bytes().endswith(b"2")
        assert tts.cached_media_type(first) == "audio/mpeg"

    def test_media_type_wav_for_local(self, tmp_path, monkeypatch):
        import audio_analysis.tts as tts

        monkeypatch.setattr(tts, "_synthesize_fish_local", lambda agent, text: b"RIFFwav")
        monkeypatch.setattr(tts, "_current_provider", lambda data_dir: "fish-local")
        path = tts.synthesize(tmp_path, 5151, "manager", "hello")
        assert path.suffix == ".wav"
        assert tts.cached_media_type(path) == "audio/wav"
