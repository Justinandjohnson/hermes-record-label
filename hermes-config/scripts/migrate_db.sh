#!/usr/bin/env bash
# =============================================================================
# AI Record Label — Database Migration Script
# =============================================================================
# Applies SQLite schema migrations from schema/migrations/ to the database.
# Tracks which migrations have been applied to avoid re-running them.
#
# Usage:
#   ./migrate_db.sh [db_path]
#
# If db_path is not provided, uses DB_PATH from environment or defaults to
# ../../data/ai_record_label.db
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MIGRATIONS_DIR="$PROJECT_ROOT/schema/migrations"

# Database path: argument > environment > default
DB_PATH="${1:-${DB_PATH:-$PROJECT_ROOT/data/ai_record_label.db}}"

# Ensure the data directory exists
DB_DIR="$(dirname "$DB_PATH")"
mkdir -p "$DB_DIR"

echo -e "${YELLOW}Applying migrations to: $DB_PATH${NC}"

# Create migrations tracking table if it doesn't exist
sqlite3 "$DB_PATH" <<'SQL'
CREATE TABLE IF NOT EXISTS _migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL UNIQUE,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
SQL

# Apply each migration in order
APPLIED=0
SKIPPED=0

for migration in "$MIGRATIONS_DIR"/*.sql; do
    if [ ! -f "$migration" ]; then
        echo -e "  ${YELLOW}!${NC} No migration files found in $MIGRATIONS_DIR"
        break
    fi

    FILENAME="$(basename "$migration")"

    # Check if already applied
    ALREADY_APPLIED=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM _migrations WHERE filename='$FILENAME';")

    if [ "$ALREADY_APPLIED" -gt 0 ]; then
        echo -e "  ${GREEN}✓${NC} $FILENAME (already applied)"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    # Apply the migration
    echo -e "  ${YELLOW}→${NC} Applying $FILENAME..."

    if sqlite3 "$DB_PATH" < "$migration"; then
        # Record that this migration was applied
        sqlite3 "$DB_PATH" "INSERT INTO _migrations (filename) VALUES ('$FILENAME');"
        echo -e "  ${GREEN}✓${NC} $FILENAME applied successfully"
        APPLIED=$((APPLIED + 1))
    else
        echo -e "  ${RED}✗${NC} $FILENAME FAILED"
        exit 1
    fi
done

# Verify WAL mode is enabled
WAL_MODE=$(sqlite3 "$DB_PATH" "PRAGMA journal_mode;")
if [ "$WAL_MODE" = "wal" ]; then
    echo -e "  ${GREEN}✓${NC} WAL mode enabled"
else
    echo -e "  ${YELLOW}!${NC} WAL mode is '$WAL_MODE', setting to WAL..."
    sqlite3 "$DB_PATH" "PRAGMA journal_mode=WAL;"
    echo -e "  ${GREEN}✓${NC} WAL mode enabled"
fi

# Show table summary
TABLE_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND substr(name, 1, 1) != '_';")
INDEX_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%';")

echo ""
echo -e "${GREEN}Migration complete:${NC} $APPLIED applied, $SKIPPED skipped"
echo -e "  Tables: $TABLE_COUNT | Indexes: $INDEX_COUNT"
echo -e "  Database: $DB_PATH"
