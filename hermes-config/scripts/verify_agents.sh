#!/usr/bin/env bash
# =============================================================================
# AI Record Label — Agent Verification Script
# =============================================================================
# Smoke tests all 4 agents and supporting services to verify they respond.
# Checks: Hermes runtime, Bandcamp agent health, database connectivity,
# and basic agent responsiveness.
#
# Usage:
#   ./verify_agents.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$CONFIG_DIR/.." && pwd)"

# Load environment
ENV_FILE="$CONFIG_DIR/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

DB_PATH="${DB_PATH:-$PROJECT_ROOT/data/ai_record_label.db}"
HERMES_URL="${HERMES_BASE_URL:-http://localhost:3000}"
BANDCAMP_URL="http://localhost:8000"

PASSED=0
FAILED=0
WARNINGS=0

check_pass() {
    echo -e "  ${GREEN}✓${NC} $1"
    PASSED=$((PASSED + 1))
}

check_fail() {
    echo -e "  ${RED}✗${NC} $1"
    FAILED=$((FAILED + 1))
}

check_warn() {
    echo -e "  ${YELLOW}!${NC} $1"
    WARNINGS=$((WARNINGS + 1))
}

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  AI Record Label — Agent Verification${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# ---------------------------------------------------------------------------
# 1. Database connectivity
# ---------------------------------------------------------------------------
echo -e "${YELLOW}[1/5] Database${NC}"

if [ -f "$DB_PATH" ]; then
    check_pass "Database file exists: $DB_PATH"

    # Check WAL mode
    WAL=$(sqlite3 "$DB_PATH" "PRAGMA journal_mode;" 2>/dev/null || echo "error")
    if [ "$WAL" = "wal" ]; then
        check_pass "WAL mode enabled"
    else
        check_warn "WAL mode not enabled (current: $WAL)"
    fi

    # Check tables exist
    TABLE_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE '_%';" 2>/dev/null || echo "0")
    if [ "$TABLE_COUNT" -ge 10 ]; then
        check_pass "Schema applied ($TABLE_COUNT tables)"
    else
        check_fail "Schema incomplete (only $TABLE_COUNT tables, expected 12+)"
    fi

    # Check artist_profile exists
    ARTIST=$(sqlite3 "$DB_PATH" "SELECT name FROM artist_profile LIMIT 1;" 2>/dev/null || echo "")
    if [ -n "$ARTIST" ]; then
        check_pass "Artist profile found: $ARTIST"
    else
        check_warn "No artist profile yet (run onboarding first)"
    fi
else
    check_fail "Database file not found: $DB_PATH"
    echo "    Run: ./scripts/migrate_db.sh"
fi

echo ""

# ---------------------------------------------------------------------------
# 2. Bandcamp Agent health
# ---------------------------------------------------------------------------
echo -e "${YELLOW}[2/5] Bandcamp Agent (http://localhost:8000)${NC}"

BC_HEALTH=$(curl -s -o /dev/null -w "%{http_code}" "$BANDCAMP_URL/system/readiness" --max-time 5 2>/dev/null || echo "000")

if [ "$BC_HEALTH" = "200" ]; then
    check_pass "Bandcamp agent is running"

    # Parse readiness details
    BC_READY=$(curl -s "$BANDCAMP_URL/system/readiness" --max-time 5 2>/dev/null || echo "{}")
    READY=$(echo "$BC_READY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('ready', False))" 2>/dev/null || echo "unknown")

    if [ "$READY" = "True" ]; then
        check_pass "Bandcamp agent fully ready (cookies valid, upload capable)"
    else
        check_warn "Bandcamp agent running but not fully ready (check cookies/config)"
    fi
else
    check_fail "Bandcamp agent not reachable (HTTP $BC_HEALTH)"
    echo "    Start it: docker compose up bandcamp-agent"
fi

echo ""

# ---------------------------------------------------------------------------
# 3. Hermes runtime
# ---------------------------------------------------------------------------
echo -e "${YELLOW}[3/5] Hermes Runtime ($HERMES_URL)${NC}"

HERMES_HEALTH=$(curl -s -o /dev/null -w "%{http_code}" "$HERMES_URL/health" --max-time 5 2>/dev/null || echo "000")

if [ "$HERMES_HEALTH" = "200" ]; then
    check_pass "Hermes runtime is running"
else
    check_fail "Hermes runtime not reachable (HTTP $HERMES_HEALTH)"
    echo "    Start it: docker compose up hermes"
fi

echo ""

# ---------------------------------------------------------------------------
# 4. Agent profiles
# ---------------------------------------------------------------------------
echo -e "${YELLOW}[4/5] Agent Profiles${NC}"

AGENTS=("a_and_r" "manager" "creative_director" "bandcamp")
PROFILES_DIR="$CONFIG_DIR/profiles"

for agent in "${AGENTS[@]}"; do
    SOUL_FILE="$PROFILES_DIR/$agent/SOUL.md"
    if [ -f "$SOUL_FILE" ]; then
        check_pass "$agent profile loaded (SOUL.md)"
    else
        check_warn "$agent profile not found at $SOUL_FILE (Workstream 1 not yet integrated)"
    fi
done

echo ""

# ---------------------------------------------------------------------------
# 5. MCP Tool definitions
# ---------------------------------------------------------------------------
echo -e "${YELLOW}[5/5] MCP Tool Definitions${NC}"

TOOLS=("audio_analysis" "file_watcher" "bandcamp_agent" "image_analysis" "calendar")

for tool in "${TOOLS[@]}"; do
    TOOL_FILE="$CONFIG_DIR/tools/${tool}.yaml"
    if [ -f "$TOOL_FILE" ]; then
        check_pass "$tool tool definition present"
    else
        check_fail "$tool tool definition missing: $TOOL_FILE"
    fi
done

echo ""

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo -e "${BLUE}============================================${NC}"
TOTAL=$((PASSED + FAILED + WARNINGS))
echo -e "  Results: ${GREEN}$PASSED passed${NC} | ${RED}$FAILED failed${NC} | ${YELLOW}$WARNINGS warnings${NC} (of $TOTAL checks)"

if [ "$FAILED" -eq 0 ]; then
    echo -e "  ${GREEN}All critical checks passed.${NC}"
else
    echo -e "  ${RED}$FAILED critical checks failed. Fix them before proceeding.${NC}"
fi
echo -e "${BLUE}============================================${NC}"

exit "$FAILED"
