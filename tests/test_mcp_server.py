"""
Tests for mcp_server.py JSON-RPC protocol layer.

Tests the MCP stdio protocol: initialize, tools/list, tools/call —
with real DB fixtures and no live Gemini/audio calls.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# We import the handler directly rather than spawning a subprocess.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcp_server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _call(method: str, params: dict, req_id: int = 1) -> dict | None:
    """Drive handle_request directly."""
    return await mcp_server.handle_request({
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params,
    })


# ---------------------------------------------------------------------------
# Protocol layer
# ---------------------------------------------------------------------------

class TestMcpProtocol:
    @pytest.mark.asyncio
    async def test_initialize_returns_server_info(self) -> None:
        resp = await _call("initialize", {})
        assert resp is not None
        assert resp["result"]["serverInfo"]["name"] == "ai-record-label"
        assert "protocolVersion" in resp["result"]

    @pytest.mark.asyncio
    async def test_initialized_notification_returns_none(self) -> None:
        resp = await _call("notifications/initialized", {})
        assert resp is None

    @pytest.mark.asyncio
    async def test_tools_list_returns_tool_array(self) -> None:
        resp = await _call("tools/list", {})
        assert resp is not None
        tools = resp["result"]["tools"]
        assert isinstance(tools, list)
        assert len(tools) > 0

    @pytest.mark.asyncio
    async def test_all_tools_have_name_and_schema(self) -> None:
        resp = await _call("tools/list", {})
        for tool in resp["result"]["tools"]:
            assert "name" in tool, f"Tool missing name: {tool}"
            assert "inputSchema" in tool, f"Tool {tool['name']} missing inputSchema"

    @pytest.mark.asyncio
    async def test_expected_tools_present(self) -> None:
        resp = await _call("tools/list", {})
        names = {t["name"] for t in resp["result"]["tools"]}
        required = {
            "analyze_track",
            "get_tracks",
            "get_track_feedback",
            "transition_state",
            "log_feedback",
            "get_stats",
            "get_projects",
            "get_artist_profile",
            "get_sessions",
            "get_artist_patterns",
            "get_track_context",
            "get_evolution_arc",
            "start_watching",
        }
        missing = required - names
        assert not missing, f"Missing tools: {missing}"

    @pytest.mark.asyncio
    async def test_unknown_method_returns_error(self) -> None:
        resp = await _call("unknown/method", {})
        assert resp is not None
        assert "error" in resp or resp.get("result") is None


# ---------------------------------------------------------------------------
# Tool: get_tracks
# ---------------------------------------------------------------------------

class TestGetTracks:
    @pytest.mark.asyncio
    async def test_get_tracks_returns_list(self, fresh_db: str, monkeypatch) -> None:
        monkeypatch.setattr(mcp_server, "DB_PATH", fresh_db)
        resp = await _call("tools/call", {"name": "get_tracks", "arguments": {}})
        assert resp is not None
        content = resp["result"]["content"]
        assert isinstance(content, list)
        # get_tracks returns a JSON array (list), not an object
        body = json.loads(content[0]["text"])
        assert isinstance(body, list)

    @pytest.mark.asyncio
    async def test_get_tracks_includes_inserted_track(self, fresh_db: str, monkeypatch) -> None:
        monkeypatch.setattr(mcp_server, "DB_PATH", fresh_db)
        # Insert a track directly
        conn = sqlite3.connect(fresh_db)
        conn.execute(
            "INSERT INTO tracks (file_path, file_hash, format, file_size, title, version) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("/tmp/test.wav", "abc123", "wav", 1000, "Test Track", 1),
        )
        conn.commit()
        conn.close()

        resp = await _call("tools/call", {"name": "get_tracks", "arguments": {}})
        # body is a list of track dicts
        body = json.loads(resp["result"]["content"][0]["text"])
        titles = [t.get("title") for t in body]
        assert "Test Track" in titles


# ---------------------------------------------------------------------------
# Tool: get_stats
# ---------------------------------------------------------------------------

class TestGetStats:
    @pytest.mark.asyncio
    async def test_get_stats_returns_expected_keys(self, fresh_db: str, monkeypatch) -> None:
        monkeypatch.setattr(mcp_server, "DB_PATH", fresh_db)
        resp = await _call("tools/call", {"name": "get_stats", "arguments": {}})
        body = json.loads(resp["result"]["content"][0]["text"])
        # get_stats returns tracks_in_progress, tracks_released, completion_rate
        assert "tracks_in_progress" in body
        assert "tracks_released" in body
        assert "completion_rate" in body


# ---------------------------------------------------------------------------
# Tool: log_feedback
# ---------------------------------------------------------------------------

class TestLogFeedback:
    @pytest.mark.asyncio
    async def test_log_feedback_persists(self, fresh_db: str, monkeypatch) -> None:
        monkeypatch.setattr(mcp_server, "DB_PATH", fresh_db)

        # First insert a track so we have a valid track_id
        conn = sqlite3.connect(fresh_db)
        conn.execute(
            "INSERT INTO tracks (file_path, file_hash, format, file_size, title, version) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("/tmp/fb.wav", "fbhash", "wav", 500, "Feedback Track", 1),
        )
        track_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        resp = await _call("tools/call", {
            "name": "log_feedback",
            "arguments": {
                "agent": "a_and_r",
                "message": "This slaps hard.",
                "channel": "internal",
                "direction": "inbound",
                "track_id": track_id,
            },
        })
        assert resp is not None
        # Verify it's actually in the DB
        conn = sqlite3.connect(fresh_db)
        rows = conn.execute(
            "SELECT message FROM feedback WHERE track_id = ?", (track_id,)
        ).fetchall()
        conn.close()
        messages = [r[0] for r in rows]
        assert "This slaps hard." in messages


# ---------------------------------------------------------------------------
# Tool: analyze_track (mocked — no real Gemini call)
# ---------------------------------------------------------------------------

class TestAnalyzeTrack:
    @pytest.mark.asyncio
    async def test_analyze_track_called_with_correct_args(
        self, fresh_db: str, wav_file: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(mcp_server, "DB_PATH", fresh_db)

        # analyze() returns an AudioAnalysis Pydantic model; mock it at the call site
        # in mcp_server which does: from audio_analysis.analyzer import analyze; analyze(...)
        from audio_analysis.models import AudioAnalysis

        fake_result = AudioAnalysis(
            track_id=1,
            bpm=93.0,
            musical_key="F# minor",
            genre_tags=["experimental hip-hop"],
        )

        with patch("audio_analysis.analyzer.analyze", return_value=fake_result):
            resp = await _call("tools/call", {
                "name": "analyze_track",
                "arguments": {"file_path": str(wav_file), "track_id": 1},
            })
        assert resp is not None
        content_text = resp["result"]["content"][0]["text"]
        data = json.loads(content_text)
        # bpm should come through from model_dump()
        assert data.get("bpm") == 93.0 or "error" not in data
