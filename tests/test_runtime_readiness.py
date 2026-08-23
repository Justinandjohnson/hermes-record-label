from __future__ import annotations

import sqlite3
from email.message import Message
from pathlib import Path

from http_api import RecordLabelHandler
from scripts.migrate_db import apply_migrations

ROOT = Path(__file__).resolve().parents[1]


def _handler(host: str, origin: str | None = None, peer: str = "127.0.0.1") -> RecordLabelHandler:
    handler = object.__new__(RecordLabelHandler)
    headers = Message()
    headers["Host"] = host
    if origin is not None:
        headers["Origin"] = origin
    handler.headers = headers
    handler.client_address = (peer, 12345)
    return handler


def test_frontend_verdict_route_is_registered() -> None:
    assert "/verdict" in RecordLabelHandler._API_PATHS


def test_token_bootstrap_accepts_only_local_app_origins() -> None:
    assert _handler("localhost:8086")._local_token_bootstrap_allowed()
    assert _handler("127.0.0.1:8086", "http://127.0.0.1:8086")._local_token_bootstrap_allowed()
    assert not _handler("192.168.1.20:8086")._local_token_bootstrap_allowed()
    assert not _handler("localhost:8086", peer="100.80.1.2")._local_token_bootstrap_allowed()
    assert not _handler(
        "localhost:8086", "https://attacker.example"
    )._local_token_bootstrap_allowed()


def test_all_migrations_apply_and_are_tracked(tmp_path: Path) -> None:
    db_path = tmp_path / "label.db"
    migrations = ROOT / "schema" / "migrations"
    applied = apply_migrations(db_path, migrations)
    assert len(applied) == len(list(migrations.glob("[0-9][0-9][0-9]_*.sql")))
    assert apply_migrations(db_path, migrations) == []

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        columns = {row[1] for row in conn.execute("PRAGMA table_info(track_audio_features)")}
        embedding_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='track_audio_embeddings'"
        ).fetchone()
    assert count == len(applied)
    assert "mode" in columns
    assert embedding_table is not None
