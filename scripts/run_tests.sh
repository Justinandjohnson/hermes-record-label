#!/usr/bin/env bash
# ============================================================================
# AI Record Label — Test Runner
# ============================================================================
# Usage:
#   ./scripts/run_tests.sh              # run all tests
#   ./scripts/run_tests.sh infra        # infrastructure + env smoke tests only
#   ./scripts/run_tests.sh unit         # unit tests only (no network, no real audio)
#   ./scripts/run_tests.sh e2e          # end-to-end pipeline tests only
#   ./scripts/run_tests.sh fast         # unit + e2e (skips live infra checks)
# ============================================================================

set -eo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PYTHON="$ROOT/.venv/bin/python"
run_pytest() { "$VENV_PYTHON" -m pytest "$@"; }

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
log() { echo -e "${CYAN}▸${NC} $1"; }
ok()  { echo -e "${GREEN}✓${NC} $1"; }

cd "$ROOT"
PATH="/opt/homebrew/bin:$PATH"

MODE="${1:-all}"

case "$MODE" in
  infra)
    log "Running infrastructure smoke tests..."
    run_pytest tests/test_infra.py -v
    ;;
  unit)
    log "Running unit tests..."
    run_pytest \
      tests/test_calendar_sync.py \
      tests/test_metadata_writer.py \
      session_intelligence/tests/ \
      file_watcher/tests/ \
      coordination/tests/ \
      audio_analysis/tests/ \
      -v
    ;;
  e2e)
    log "Running end-to-end pipeline tests..."
    run_pytest tests/test_pipeline_e2e.py -v
    ;;
  mcp)
    log "Running MCP server tests..."
    run_pytest tests/test_mcp_server.py -v
    ;;
  fast)
    log "Running fast tests (unit + e2e, no live checks)..."
    run_pytest \
      tests/test_calendar_sync.py \
      tests/test_metadata_writer.py \
      tests/test_pipeline_e2e.py \
      tests/test_mcp_server.py \
      session_intelligence/tests/ \
      file_watcher/tests/ \
      coordination/tests/ \
      audio_analysis/tests/ \
      -v
    ;;
  all|*)
    log "Running full test suite..."
    run_pytest -v --tb=short
    ;;
esac

ok "Done."
