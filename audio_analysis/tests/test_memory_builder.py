"""Tests for the memory builder module."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from audio_analysis.memory_builder import (
    _apply_memory_updates,
    _ensure_tables,
    _get_connection,
    _load_all_analyses,
    _load_memory_entries,
    build_memory,
    get_artist_patterns,
    get_evolution_arc,
    get_strengths_and_weaknesses,
    get_track_context,
    store_analysis_sync,
)
from audio_analysis.models import (
    AudioAnalysis,
    AudioMemoryEntry,
    EnergyCurvePoint,
    MemoryCategory,
    MixObservation,
    NotableMoment,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "test_memory.db")
    conn = _get_connection(path)
    _ensure_tables(conn)
    conn.close()
    return path


@pytest.fixture()
def sample_analysis() -> AudioAnalysis:
    return AudioAnalysis(
        track_id=1,
        model_used="gemini-3.1-pro",
        bpm=120.0,
        musical_key="C minor",
        energy_curve=[
            EnergyCurvePoint(timestamp="0:00", energy_level=0.3),
            EnergyCurvePoint(timestamp="0:30", energy_level=0.7),
        ],
        structure={"intro": "0:00-0:15", "verse1": "0:15-1:00"},
        instruments=["808 kick", "synth pad"],
        genre_tags=["trap", "ambient"],
        mood_tags=["dark", "atmospheric"],
        mix_observations=[
            MixObservation(timestamp="0:00", observation="Heavy low end"),
        ],
        notable_moments=[
            NotableMoment(
                timestamp="0:30",
                description="Effective drop",
                quality_judgment="strength",
            ),
        ],
        raw_response="{}",
    )


# ---------------------------------------------------------------------------
# store_analysis_sync
# ---------------------------------------------------------------------------


class TestStoreAnalysis:
    def test_stores_and_retrieves(self, db_path: str, sample_analysis: AudioAnalysis) -> None:
        row_id = store_analysis_sync(db_path, track_id=1, analysis=sample_analysis)
        assert row_id >= 1

        conn = _get_connection(db_path)
        row = conn.execute("SELECT * FROM audio_analyses WHERE id = ?", (row_id,)).fetchone()
        conn.close()

        assert row["track_id"] == 1
        assert row["bpm"] == 120.0
        assert row["musical_key"] == "C minor"
        instruments = json.loads(row["instruments"])
        assert "808 kick" in instruments

    def test_load_all_analyses(self, db_path: str, sample_analysis: AudioAnalysis) -> None:
        store_analysis_sync(db_path, 1, sample_analysis)
        store_analysis_sync(db_path, 2, sample_analysis)

        conn = _get_connection(db_path)
        results = _load_all_analyses(conn)
        conn.close()

        assert len(results) == 2
        assert results[0]["track_id"] == 1
        assert results[1]["track_id"] == 2


# ---------------------------------------------------------------------------
# _apply_memory_updates
# ---------------------------------------------------------------------------


class TestApplyMemoryUpdates:
    def test_insert_new_entry(self, db_path: str) -> None:
        conn = _get_connection(db_path)
        pattern_data = {
            "updated_entries": [
                {
                    "id": None,
                    "category": "signature_sound",
                    "observation": "Uses detuned 808s as a defining element",
                    "confidence": 0.4,
                    "reasoning": "Prominent in first track",
                }
            ]
        }

        _apply_memory_updates(conn, track_id=1, pattern_data=pattern_data)

        entries = _load_memory_entries(conn)
        conn.close()

        assert len(entries) == 1
        assert entries[0].category == MemoryCategory.SIGNATURE_SOUND
        assert entries[0].confidence == 0.4
        assert entries[0].first_noticed_track_id == 1
        assert entries[0].times_observed == 1

    def test_update_existing_entry(self, db_path: str) -> None:
        conn = _get_connection(db_path)
        # Insert an initial entry
        conn.execute(
            """INSERT INTO audio_memory
               (category, observation, confidence, first_noticed_track_id,
                supporting_track_ids, times_observed)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("recurring_strength", "Strong hooks", 0.4, 1, json.dumps([1]), 1),
        )
        conn.commit()
        entry_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Update with second track confirmation
        pattern_data = {
            "updated_entries": [
                {
                    "id": entry_id,
                    "category": "recurring_strength",
                    "observation": "Strong hooks, especially in choruses",
                    "confidence": 0.6,
                    "reasoning": "Confirmed in second track",
                }
            ]
        }

        _apply_memory_updates(conn, track_id=2, pattern_data=pattern_data)

        entries = _load_memory_entries(conn)
        conn.close()

        assert len(entries) == 1
        assert entries[0].confidence == 0.6
        assert entries[0].times_observed == 2
        assert 1 in entries[0].supporting_track_ids
        assert 2 in entries[0].supporting_track_ids

    def test_no_duplicate_track_ids(self, db_path: str) -> None:
        conn = _get_connection(db_path)
        conn.execute(
            """INSERT INTO audio_memory
               (category, observation, confidence, first_noticed_track_id,
                supporting_track_ids, times_observed)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("production_pattern", "Uses sidechain", 0.5, 1, json.dumps([1, 2]), 2),
        )
        conn.commit()
        entry_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Try to add track_id=2 again
        pattern_data = {
            "updated_entries": [
                {
                    "id": entry_id,
                    "category": "production_pattern",
                    "observation": "Uses sidechain compression",
                    "confidence": 0.7,
                }
            ]
        }
        _apply_memory_updates(conn, track_id=2, pattern_data=pattern_data)

        entries = _load_memory_entries(conn)
        conn.close()

        # track_id 2 should not be duplicated
        assert entries[0].supporting_track_ids.count(2) == 1


# ---------------------------------------------------------------------------
# Query interface
# ---------------------------------------------------------------------------


def _seed_memory(db_path: str) -> None:
    """Insert a set of memory entries for query tests."""
    conn = _get_connection(db_path)
    entries = [
        ("signature_sound", "Detuned 808 patterns", 0.8, 1, json.dumps([1, 2, 3]), 3),
        ("recurring_strength", "Strong melodic hooks", 0.7, 1, json.dumps([1, 3]), 2),
        ("recurring_weakness", "Bridges lose energy", 0.6, 2, json.dumps([2, 4]), 2),
        ("evolution_note", "Moving toward darker production", 0.5, 3, json.dumps([3, 4]), 2),
        ("genre_tendency", "Gravitates toward lo-fi hip hop", 0.75, 1, json.dumps([1, 2, 3, 4]), 4),
        ("production_pattern", "Heavy sidechain on pads", 0.6, 2, json.dumps([2, 3]), 2),
    ]
    for cat, obs, conf, first, supporting, times in entries:
        conn.execute(
            """INSERT INTO audio_memory
               (category, observation, confidence, first_noticed_track_id,
                supporting_track_ids, times_observed)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (cat, obs, conf, first, supporting, times),
        )
    conn.commit()
    conn.close()


class TestGetArtistPatterns:
    def test_returns_grouped_patterns(self, db_path: str) -> None:
        _seed_memory(db_path)
        patterns = get_artist_patterns(db_path)

        assert len(patterns.signature_sounds) == 1
        assert len(patterns.strengths) == 1
        assert len(patterns.weaknesses) == 1
        assert len(patterns.evolution_notes) == 1
        assert len(patterns.genre_tendencies) == 1
        assert len(patterns.production_patterns) == 1
        assert len(patterns.all_entries) == 6

    def test_sorted_by_confidence(self, db_path: str) -> None:
        _seed_memory(db_path)
        patterns = get_artist_patterns(db_path)

        confidences = [e.confidence for e in patterns.all_entries]
        assert confidences == sorted(confidences, reverse=True)

    def test_empty_db(self, db_path: str) -> None:
        patterns = get_artist_patterns(db_path)
        assert len(patterns.all_entries) == 0


class TestGetTrackContext:
    def test_returns_context_for_track(self, db_path: str) -> None:
        _seed_memory(db_path)
        ctx = get_track_context(db_path, track_id=1)

        assert ctx.track_id == 1
        # Track 1 is first_noticed for signature_sound, recurring_strength, genre_tendency
        assert len(ctx.new_observations) == 3
        # Track 1 is in supporting_track_ids for those same entries
        assert len(ctx.confirmed_patterns) >= 3

    def test_unknown_track(self, db_path: str) -> None:
        _seed_memory(db_path)
        ctx = get_track_context(db_path, track_id=999)
        assert ctx.track_id == 999
        assert len(ctx.new_observations) == 0
        assert len(ctx.confirmed_patterns) == 0


class TestGetEvolutionArc:
    def test_returns_evolution_entries(self, db_path: str) -> None:
        _seed_memory(db_path)
        arc = get_evolution_arc(db_path)

        assert len(arc) == 1
        assert arc[0].category == MemoryCategory.EVOLUTION_NOTE

    def test_empty_arc(self, db_path: str) -> None:
        arc = get_evolution_arc(db_path)
        assert arc == []


class TestGetStrengthsAndWeaknesses:
    def test_returns_both(self, db_path: str) -> None:
        _seed_memory(db_path)
        result = get_strengths_and_weaknesses(db_path)

        assert len(result["strengths"]) == 1
        assert len(result["weaknesses"]) == 1
        assert result["strengths"][0].category == MemoryCategory.RECURRING_STRENGTH
        assert result["weaknesses"][0].category == MemoryCategory.RECURRING_WEAKNESS


# ---------------------------------------------------------------------------
# build_memory (integration with mocked OpenRouter)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_memory_full_flow(db_path: str, sample_analysis: AudioAnalysis) -> None:
    """Test the full memory building pipeline with mocked OpenRouter."""
    memory_response = json.dumps(
        {
            "updated_entries": [
                {
                    "id": None,
                    "category": "instrument_palette",
                    "observation": "Favors 808 kick and synth pads",
                    "confidence": 0.35,
                    "reasoning": "Both present in first analysis",
                }
            ],
            "track_context": {
                "similarities_to_past": [],
                "departures_from_past": [],
                "evolution_notes": ["First track analyzed"],
                "confirmed_patterns": [],
                "new_observations": ["Heavy use of 808 patterns"],
            },
        }
    )

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": memory_response}}]},
                request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
            )

    with patch("audio_analysis.memory_builder.httpx.AsyncClient", MockAsyncClient), \
         patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
        ctx = await build_memory(db_path, track_id=1, analysis=sample_analysis)

    assert ctx.track_id == 1
    assert "First track analyzed" in ctx.evolution_notes
    assert "Heavy use of 808 patterns" in ctx.new_observations

    # Verify DB state
    conn = _get_connection(db_path)
    analyses = conn.execute("SELECT COUNT(*) FROM audio_analyses").fetchone()[0]
    memory = conn.execute("SELECT COUNT(*) FROM audio_memory").fetchone()[0]
    conn.close()

    assert analyses == 1
    assert memory == 1
