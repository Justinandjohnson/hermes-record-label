"""Apply every SQLite schema migration, including to legacy untracked databases."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def _statements(sql: str) -> list[str]:
    statements: list[str] = []
    pending = ""
    for line in sql.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            if pending.strip():
                statements.append(pending)
            pending = ""
    if pending.strip():
        raise ValueError("Incomplete SQL statement at end of migration")
    return statements


def apply_migrations(db_path: Path, migrations_dir: Path) -> list[str]:
    migrations = sorted(migrations_dir.glob("[0-9][0-9][0-9]_*.sql"))
    if not migrations:
        raise FileNotFoundError(f"No migrations found in {migrations_dir}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    applied: list[str] = []
    with sqlite3.connect(db_path, timeout=60) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        recorded = {row[0] for row in conn.execute("SELECT name FROM schema_migrations").fetchall()}
        for migration in migrations:
            if migration.name in recorded:
                continue
            for statement in _statements(migration.read_text(encoding="utf-8")):
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError as exc:
                    # Legacy databases may have the column but no migration ledger.
                    if "duplicate column name" not in str(exc).lower():
                        raise
            conn.execute("INSERT INTO schema_migrations(name) VALUES (?)", (migration.name,))
            conn.commit()
            applied.append(migration.name)
    return applied


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("db_path", type=Path)
    parser.add_argument(
        "--migrations",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "schema" / "migrations",
    )
    args = parser.parse_args()
    applied = apply_migrations(args.db_path.resolve(), args.migrations.resolve())
    print(f"Applied {len(applied)} migration(s)")


if __name__ == "__main__":
    main()
