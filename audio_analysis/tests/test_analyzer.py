"""Tests for the audio analysis pipeline."""

from __future__ import annotations

import json
import sqlite3
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from audio_analysis.analyzer import AnalyzerError, analyze_async
from audio_analysis.gemini_client import (
    FileTooLargeError,
    GeminiClientError,
    UnsupportedFormatError,
    _sanitize_loop_like_analysis,
    validate_audio_file,
)
from audio_analysis.models import AudioAnalysis, MixObservation, NotableMoment

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_GEMINI_RESPONSE = json.dumps(
    {
        "bpm": 128.0,
        "musical_key": "A minor",
        "energy_curve": [
            {"timestamp": "0:00", "energy_level": 0.2},
            {"timestamp": "0:30", "energy_level": 0.5},
            {"timestamp": "1:00", "energy_level": 0.8},
            {"timestamp": "1:30", "energy_level": 0.6},
        ],
        "structure": {
            "intro": "0:00-0:15",
            "verse1": "0:15-0:45",
            "chorus1": "0:45-1:15",
            "outro": "1:15-1:30",
        },
        "instruments": ["808 kick", "hi-hats", "synth pad", "vocal chop"],
        "genre_tags": ["lo-fi hip hop", "chillhop"],
        "mood_tags": ["melancholic", "nocturnal", "introspective"],
        "mix_observations": [
            {
                "timestamp": "0:00",
                "observation": "Low end is prominent, 808 sits well in the mix",
            },
            {
                "timestamp": "0:45",
                "observation": "Vocal chop panned slightly right, creates nice width",
            },
        ],
        "notable_moments": [
            {
                "timestamp": "0:45",
                "description": "Beat switch into the chorus is effective",
                "quality_judgment": "strength",
            },
            {
                "timestamp": "1:15",
                "description": "Outro feels abrupt, could use a longer tail",
                "quality_judgment": "weakness",
            },
        ],
    }
)

SAMPLE_MEMORY_RESPONSE = json.dumps(
    {
        "updated_entries": [
            {
                "id": None,
                "category": "production_pattern",
                "observation": "Relies heavily on 808 kick patterns",
                "confidence": 0.3,
                "reasoning": "First track, but the 808 is central to the arrangement",
            }
        ],
        "track_context": {
            "similarities_to_past": [],
            "departures_from_past": [],
            "evolution_notes": [],
            "confirmed_patterns": [],
            "new_observations": ["Uses 808 as primary rhythmic foundation"],
        },
    }
)


@pytest.fixture()
def tmp_audio_file(tmp_path: Path) -> Path:
    """Create a valid short mono WAV file for validation tests."""
    wav = tmp_path / "test_track.wav"
    with wave.open(str(wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(b"\x00\x00" * 22050)
    return wav


@pytest.fixture()
def tmp_db(tmp_path: Path) -> str:
    """Return a path to a temporary SQLite database."""
    return str(tmp_path / "test.db")


# ---------------------------------------------------------------------------
# validate_audio_file tests
# ---------------------------------------------------------------------------


class TestValidateAudioFile:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            validate_audio_file(tmp_path / "nonexistent.wav")

    def test_unsupported_format(self, tmp_path: Path) -> None:
        f = tmp_path / "track.m4a"
        f.write_bytes(b"\x00" * 100)
        with pytest.raises(UnsupportedFormatError):
            validate_audio_file(f)

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.wav"
        f.write_bytes(b"")
        with pytest.raises(GeminiClientError, match="empty"):
            validate_audio_file(f)

    def test_file_too_large(self, tmp_path: Path) -> None:
        f = tmp_path / "huge.wav"
        # Create a file just over 50 MB
        f.write_bytes(b"\x00" * (50 * 1024 * 1024 + 1))
        with pytest.raises(FileTooLargeError):
            validate_audio_file(f)

    @pytest.mark.parametrize("ext", [".wav", ".flac", ".mp3", ".aiff", ".aif", ".ogg"])
    def test_supported_formats(self, tmp_path: Path, ext: str) -> None:
        f = tmp_path / f"track{ext}"
        f.write_bytes(b"\x00" * 100)
        mime = validate_audio_file(f)
        assert mime.startswith("audio/")


# ---------------------------------------------------------------------------
# analyze_async tests
# ---------------------------------------------------------------------------


def _mock_gemini_response(text: str) -> MagicMock:
    """Create a mock Gemini generate_content response."""
    resp = MagicMock()
    resp.text = text
    return resp


def _openrouter_client_factory(contents: list[str | Exception]):
    calls = {"count": 0}

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            calls["count"] += 1
            item = contents.pop(0)
            if isinstance(item, Exception):
                raise item
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": item}}]},
                request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
            )

    MockAsyncClient.calls = calls
    return MockAsyncClient


@pytest.mark.asyncio
async def test_analyze_async_full_pipeline(tmp_audio_file: Path, tmp_db: str) -> None:
    """Test the full analysis pipeline with mocked Gemini calls."""
    client = _openrouter_client_factory([SAMPLE_GEMINI_RESPONSE, SAMPLE_MEMORY_RESPONSE])

    with patch("audio_analysis.gemini_client.httpx.AsyncClient", client), \
         patch("audio_analysis.memory_builder.httpx.AsyncClient", client), \
         patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
        result = await analyze_async(
            str(tmp_audio_file),
            tmp_db,
            track_id=42,
        )

    assert isinstance(result, AudioAnalysis)
    assert result.bpm == 128.0
    assert result.musical_key == "A minor"
    assert len(result.energy_curve) == 4
    assert len(result.instruments) == 4
    assert "lo-fi hip hop" in result.genre_tags
    assert len(result.notable_moments) == 2
    assert result.track_id == 42

    # Verify DB was populated
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM audio_analyses").fetchall()
    assert len(rows) == 1
    assert rows[0]["track_id"] == 42
    assert rows[0]["bpm"] == 128.0

    mem_rows = conn.execute("SELECT * FROM audio_memory").fetchall()
    assert len(mem_rows) == 1
    assert mem_rows[0]["category"] == "production_pattern"
    conn.close()


@pytest.mark.asyncio
async def test_analyze_async_skip_memory(tmp_audio_file: Path, tmp_db: str) -> None:
    """Test analysis with memory building skipped."""
    client = _openrouter_client_factory([SAMPLE_GEMINI_RESPONSE])

    with patch("audio_analysis.gemini_client.httpx.AsyncClient", client), \
         patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
        result = await analyze_async(
            str(tmp_audio_file),
            tmp_db,
            track_id=1,
            skip_memory=True,
        )

    assert result.bpm == 128.0

    assert client.calls["count"] == 1


@pytest.mark.asyncio
async def test_analyze_async_invalid_file(tmp_path: Path, tmp_db: str) -> None:
    """Test that invalid files raise AnalyzerError."""
    with pytest.raises(AnalyzerError, match="not found"):
        await analyze_async(str(tmp_path / "nope.wav"), tmp_db)


@pytest.mark.asyncio
async def test_analyze_async_gemini_failure(tmp_audio_file: Path, tmp_db: str) -> None:
    """Test graceful handling of Gemini API failure."""
    client = _openrouter_client_factory([httpx.TimeoutException("API timeout")])

    with (
        patch("audio_analysis.gemini_client.httpx.AsyncClient", client),
        patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}),
        pytest.raises(AnalyzerError, match="Audio analysis failed"),
    ):
        await analyze_async(str(tmp_audio_file), tmp_db, track_id=1)


@pytest.mark.asyncio
async def test_analyze_async_bad_json_response(tmp_audio_file: Path, tmp_db: str) -> None:
    """Test handling of non-JSON Gemini response."""
    client = _openrouter_client_factory(["This is not JSON at all"])

    with (
        patch("audio_analysis.gemini_client.httpx.AsyncClient", client),
        patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}),
        pytest.raises(AnalyzerError, match="Audio analysis failed"),
    ):
        await analyze_async(str(tmp_audio_file), tmp_db, track_id=1)


def test_sanitize_loop_like_analysis_removes_section_claims() -> None:
    analysis = AudioAnalysis(
        notable_moments=[
            NotableMoment(
                timestamp="0:13",
                description="The intro beat drop lands hard and opens the verse cleanly",
                quality_judgment="strength",
            ),
            NotableMoment(
                timestamp="1:22",
                description="Dust texture stays consistent and believable",
                quality_judgment="interesting",
            ),
        ],
        mix_observations=[
            MixObservation(timestamp="0:00", observation="Low-mid warmth is steady throughout"),
        ],
        structure={"intro": "0:00-0:13", "verse1": "0:13-0:40"},
    )

    result = _sanitize_loop_like_analysis(analysis)

    assert result.structure == {}
    assert len(result.notable_moments) == 1
    assert "verse" not in result.notable_moments[0].description.lower()
    assert "loop-based arrangement" in result.mix_observations[0].observation.lower()


@pytest.mark.asyncio
async def test_analyze_async_memory_failure_still_stores(
    tmp_audio_file: Path, tmp_db: str
) -> None:
    """If memory building fails, the analysis should still be stored."""
    client = _openrouter_client_factory(
        [SAMPLE_GEMINI_RESPONSE, httpx.TimeoutException("Memory prompt failed")]
    )

    with patch("audio_analysis.gemini_client.httpx.AsyncClient", client), \
         patch("audio_analysis.memory_builder.httpx.AsyncClient", client), \
         patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
        result = await analyze_async(str(tmp_audio_file), tmp_db, track_id=7)

    assert result.bpm == 128.0

    # Analysis should still be in DB even though memory building failed
    conn = sqlite3.connect(tmp_db)
    rows = conn.execute("SELECT COUNT(*) FROM audio_analyses").fetchone()
    assert rows[0] >= 1
    conn.close()
