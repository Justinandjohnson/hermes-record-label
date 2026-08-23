"""Tests for durable actions and truthful publishing state."""

from __future__ import annotations

import sqlite3
import wave
from pathlib import Path

import pytest

from evals.action_harness import run_action_eval
from file_watcher.track_registry import ensure_schema
from file_watcher.watcher import FileWatcherService
from publishing.higgsfield_plan import (
    _model_summary,
    _validate_aspect_ratio,
    _validate_model_params,
    approve_and_expand,
    build_plan,
)
from publishing.tiktok import MIB, _source_info, direct_post_video
from publishing.youtube import _channel_identity, upload_video


def _write_wav(path: Path) -> None:
    frames = (b"\x00\x00\x10\x00\xf0\xff\x00\x00") * 12_000
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(48_000)
        audio.writeframes(frames)


def test_action_harness_requires_observable_side_effects(tmp_path: Path) -> None:
    audio = tmp_path / "acceptance.wav"
    _write_wav(audio)
    report = run_action_eval(audio, tmp_path / "results")
    assert report["verdict"] == "action_backed"
    assert report["metrics"]["verified_actions"] == report["metrics"]["required_actions"]
    assert report["quality_verdict"] == "within_budgets"
    assert report["timings_ms"]["total"] >= report["timings_ms"]["validation"]
    assert Path(report["run_dir"], "report.json").is_file()


def test_vault_copy_survives_dispatcher_failure(tmp_path: Path) -> None:
    audio = tmp_path / "track.wav"
    vault = tmp_path / "vault"
    db_path = tmp_path / "hermes.db"
    _write_wav(audio)
    conn = sqlite3.connect(db_path)
    ensure_schema(conn)

    def fail_dispatch(_event: str, _payload: dict[str, object]) -> None:
        raise RuntimeError("downstream unavailable")

    service = FileWatcherService(
        watch_dir=tmp_path,
        db_path=db_path,
        emit=fail_dispatch,
        sync_destinations=[vault],
    )
    with pytest.raises(RuntimeError, match="downstream unavailable"):
        service._process_file(str(audio), conn)
    conn.close()
    assert (vault / audio.name).read_bytes() == audio.read_bytes()


def test_higgsfield_plan_is_twenty_unique_unclaimed_jobs() -> None:
    plan = build_plan("A red room performance", duration_seconds=100)
    assert len(plan) == 20
    assert len({item["prompt"] for item in plan}) == 20
    assert {item["status"] for item in plan} == {"planned"}


def test_higgsfield_expansion_requires_artist_approval() -> None:
    with pytest.raises(PermissionError, match="Artist approval"):
        approve_and_expand({}, 1)


def test_higgsfield_live_profile_constraints_are_enforced() -> None:
    model = _model_summary(
        {
            "id": "current_model",
            "output_type": "video",
            "aspect_ratios": ["9:16"],
            "parameters": [
                {"name": "duration", "type": "number", "min": 4, "max": 10},
                {"name": "mode", "options": ["t2v", "omni_reference"]},
            ],
        }
    )
    _validate_aspect_ratio(model, "9:16")
    _validate_model_params(model, {"duration": 5, "mode": "t2v"}, 1)
    with pytest.raises(ValueError, match="does not support aspect ratio"):
        _validate_aspect_ratio(model, "16:9")
    with pytest.raises(ValueError, match="model minimum"):
        _validate_model_params(model, {"duration": 3}, 1)


def test_youtube_channel_identity_accepts_id_title_and_handle() -> None:
    identity = _channel_identity(
        {"id": "UC123", "snippet": {"title": "Artist Channel", "customUrl": "@Artist"}}
    )
    assert identity == {"uc123", "artist channel", "artist"}


@pytest.mark.parametrize(
    ("size", "count", "chunk_size"),
    [(4 * MIB, 1, 4 * MIB), (128 * MIB, 1, 128 * MIB), (129 * MIB, 2, 64 * MIB)],
)
def test_tiktok_chunk_plan(size: int, count: int, chunk_size: int) -> None:
    source = _source_info(size)
    assert source["total_chunk_count"] == count
    assert source["chunk_size"] == chunk_size


def test_external_uploads_require_artist_approval(tmp_path: Path) -> None:
    with pytest.raises(PermissionError, match="Artist approval"):
        upload_video(
            tmp_path / "missing.mp4",
            token_path=tmp_path / "token.json",
            title="x",
            description="",
            expected_channel="@artist",
        )
    with pytest.raises(PermissionError, match="Artist approval"):
        direct_post_video(tmp_path / "missing.mp4", access_token="not-used", title="x")
