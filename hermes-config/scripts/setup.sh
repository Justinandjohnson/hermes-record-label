#!/usr/bin/env bash
# =============================================================================
# AI Record Label — Full Setup Script
# =============================================================================
# Checks dependencies, creates directories, applies database migrations,
# verifies environment variables, and prepares the system for first run.
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh
# =============================================================================

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Project root (relative to this script's location)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONFIG_DIR="$PROJECT_ROOT/hermes-config"

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  AI Record Label — Setup${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Check dependencies
# ---------------------------------------------------------------------------
echo -e "${YELLOW}[1/6] Checking dependencies...${NC}"

MISSING_DEPS=0

check_dep() {
    if command -v "$1" &> /dev/null; then
        echo -e "  ${GREEN}✓${NC} $1 found: $(command -v "$1")"
    else
        echo -e "  ${RED}✗${NC} $1 not found — $2"
        MISSING_DEPS=$((MISSING_DEPS + 1))
    fi
}

check_dep "docker" "Install Docker: https://docs.docker.com/get-docker/"
check_dep "docker" "Docker Compose is bundled with Docker Desktop"
check_dep "sqlite3" "Install: brew install sqlite3 (macOS) or apt install sqlite3 (Linux)"
check_dep "python3" "Install Python 3.10+: https://python.org"
check_dep "curl" "Install: should be pre-installed on most systems"

# Check Docker Compose (v2 is a docker subcommand)
if docker compose version &> /dev/null 2>&1; then
    echo -e "  ${GREEN}✓${NC} docker compose available"
else
    echo -e "  ${RED}✗${NC} docker compose not available — update Docker Desktop"
    MISSING_DEPS=$((MISSING_DEPS + 1))
fi

if [ "$MISSING_DEPS" -gt 0 ]; then
    echo ""
    echo -e "${RED}Missing $MISSING_DEPS dependencies. Install them and re-run.${NC}"
    exit 1
fi

echo ""

# ---------------------------------------------------------------------------
# Step 2: Check environment file
# ---------------------------------------------------------------------------
echo -e "${YELLOW}[2/6] Checking environment configuration...${NC}"

ENV_FILE="$CONFIG_DIR/.env"
ENV_EXAMPLE="$CONFIG_DIR/.env.example"

if [ ! -f "$ENV_FILE" ]; then
    echo -e "  ${YELLOW}!${NC} No .env file found. Copying from .env.example..."
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    echo -e "  ${YELLOW}!${NC} Please edit $ENV_FILE with your actual values."
    echo ""
    echo -e "  ${RED}Required variables:${NC}"
    echo "    OPENROUTER_API_KEY    — https://openrouter.ai/keys"
    echo "    GEMINI_API_KEY        — https://aistudio.google.com/apikey"
    echo "    TWILIO_ACCOUNT_SID    — https://console.twilio.com"
    echo "    TWILIO_AUTH_TOKEN     — https://console.twilio.com"
    echo "    TWILIO_PHONE_NUMBER   — Your Twilio phone number"
    echo "    ARTIST_PHONE_NUMBER   — Artist's phone number"
    echo "    BACKEND_API_TOKEN     — Any secure random string"
    echo "    BCA_ARTIST_URL        — Your Bandcamp artist URL"
    echo "    WATCH_FOLDER          — Path to your DAW export folder"
    echo ""
    echo -e "  ${YELLOW}Fill in .env and re-run this script.${NC}"
    exit 1
fi

# Verify required vars are set (not empty)
echo -e "  ${GREEN}✓${NC} .env file found"

REQUIRED_VARS=(
    "OPENROUTER_API_KEY"
    "GEMINI_API_KEY"
    "TWILIO_ACCOUNT_SID"
    "TWILIO_AUTH_TOKEN"
    "TWILIO_PHONE_NUMBER"
    "ARTIST_PHONE_NUMBER"
    "BACKEND_API_TOKEN"
    "BCA_ARTIST_URL"
    "WATCH_FOLDER"
)

# Source the env file to check values
set -a
source "$ENV_FILE"
set +a

MISSING_VARS=0
for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var:-}" ]; then
        echo -e "  ${RED}✗${NC} $var is not set"
        MISSING_VARS=$((MISSING_VARS + 1))
    else
        # Mask the value for display
        VALUE="${!var}"
        MASKED="${VALUE:0:4}****"
        echo -e "  ${GREEN}✓${NC} $var = $MASKED"
    fi
done

if [ "$MISSING_VARS" -gt 0 ]; then
    echo ""
    echo -e "${RED}$MISSING_VARS required variables are missing. Edit $ENV_FILE and re-run.${NC}"
    exit 1
fi

echo ""

# ---------------------------------------------------------------------------
# Step 3: Create required directories
# ---------------------------------------------------------------------------
echo -e "${YELLOW}[3/6] Creating directories...${NC}"

DIRS=(
    "$PROJECT_ROOT/hermes-config/profiles"
    "$PROJECT_ROOT/data"
    "$PROJECT_ROOT/watch"
)

for dir in "${DIRS[@]}"; do
    if [ ! -d "$dir" ]; then
        mkdir -p "$dir"
        echo -e "  ${GREEN}+${NC} Created $dir"
    else
        echo -e "  ${GREEN}✓${NC} $dir exists"
    fi
done

echo ""

# ---------------------------------------------------------------------------
# Step 4: Apply database migrations
# ---------------------------------------------------------------------------
echo -e "${YELLOW}[4/6] Applying database migrations...${NC}"

DB_PATH="${DB_PATH:-$PROJECT_ROOT/data/ai_record_label.db}"

# Run the migration script
"$SCRIPT_DIR/migrate_db.sh" "$DB_PATH"

echo ""

# ---------------------------------------------------------------------------
# Step 5: Validate watch folder
# ---------------------------------------------------------------------------
echo -e "${YELLOW}[5/6] Validating watch folder...${NC}"

if [ -d "$WATCH_FOLDER" ]; then
    echo -e "  ${GREEN}✓${NC} Watch folder exists: $WATCH_FOLDER"
else
    echo -e "  ${YELLOW}!${NC} Watch folder does not exist: $WATCH_FOLDER"
    echo -e "  ${YELLOW}!${NC} Creating it now..."
    mkdir -p "$WATCH_FOLDER"
    echo -e "  ${GREEN}+${NC} Created $WATCH_FOLDER"
fi

echo ""

# ---------------------------------------------------------------------------
# Step 6: Summary
# ---------------------------------------------------------------------------
echo -e "${BLUE}============================================${NC}"
echo -e "${GREEN}  Setup Complete${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""
echo "  Database:     $DB_PATH"
echo "  Watch folder: $WATCH_FOLDER"
echo "  Config:       $CONFIG_DIR"
echo ""
echo "Next steps:"
echo "  1. Start the services:  cd hermes-config && docker compose up -d"
echo "  2. Test SMS:            ./scripts/test_sms.sh"
echo "  3. Verify agents:       ./scripts/verify_agents.sh"
echo ""
echo "  Or run the full stack:  docker compose up"
echo ""
