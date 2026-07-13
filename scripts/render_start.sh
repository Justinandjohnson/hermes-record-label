#!/usr/bin/env bash
# =============================================================================
# Render.com startup script
# 1. Restores SQLite DB from B2 via Litestream (gets latest Mac state)
# 2. Applies any pending migrations (idempotent)
# 3. Starts the HTTP API server
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${AI_RECORD_LABEL_DATA:-/data}"
DB_FILE="$DATA_DIR/hermes.db"
LITESTREAM_BIN="$ROOT/.litestream/litestream"
LITESTREAM_CFG="$ROOT/litestream.yml"

mkdir -p "$DATA_DIR"

echo "── AI Record Label — Cloud Startup ─────────────────────"
echo "  Data dir : $DATA_DIR"
echo "  DB file  : $DB_FILE"

# ── 1. Restore DB from B2 via Litestream ──────────────────────────────────
if [[ -x "$LITESTREAM_BIN" && -f "$LITESTREAM_CFG" ]]; then
  if [[ -n "${B2_WRITE_KEY_ID:-}" && -n "${B2_WRITE_APPLICATION_KEY:-}" ]]; then
    echo "  Restoring DB from B2 replica..."
    # -if-replica-exists: silently skip if no replica yet (first deploy)
    "$LITESTREAM_BIN" restore \
      -config "$LITESTREAM_CFG" \
      -if-replica-exists \
      "$DB_FILE" 2>&1 || true
    echo "  DB restore complete (or no replica yet — starting fresh)"
  else
    echo "  WARN: B2 credentials not set — skipping Litestream restore"
    echo "  Set B2_WRITE_KEY_ID and B2_WRITE_APPLICATION_KEY in Render env vars"
  fi
else
  echo "  WARN: Litestream not found — DB won't be synced from Mac"
fi

# ── 2. Apply migrations (idempotent) ──────────────────────────────────────
if command -v sqlite3 &>/dev/null; then
  echo "  Applying migrations..."
  for migration in "$ROOT"/schema/migrations/*.sql; do
    sqlite3 "$DB_FILE" < "$migration" 2>/dev/null || true
  done
  echo "  Migrations applied"
else
  echo "  WARN: sqlite3 not found — applying migrations via Python"
  python3 - << 'EOF'
import sqlite3, glob, pathlib, os
db = os.environ.get("AI_RECORD_LABEL_DATA", "/data") + "/hermes.db"
root = pathlib.Path(__file__).resolve().parent.parent if "__file__" in dir() else pathlib.Path(".")
for m in sorted(pathlib.Path("/opt/render/project/src/schema/migrations").glob("*.sql")):
    try:
        sqlite3.connect(db).executescript(m.read_text())
    except Exception:
        pass
EOF
fi

# ── 3. Start HTTP API ──────────────────────────────────────────────────────
echo "  Starting HTTP API on port ${PORT:-10000}..."
cd "$ROOT"
exec python http_api.py
