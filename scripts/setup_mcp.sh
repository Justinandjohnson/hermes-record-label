#!/usr/bin/env bash
# =============================================================================
# setup_mcp.sh — Wire up external MCP integrations for the AI Record Label
# =============================================================================
# Run this once to authenticate Google Calendar and set your Perplexity key.
# Everything else (Playwright, iMessage) works without this script.
# =============================================================================

set -euo pipefail

HERMES_DIR="$HOME/.hermes"
GOOGLE_DIR="$HERMES_DIR/google"
ENV_FILE="$HERMES_DIR/.env"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  AI Record Label — MCP Integration Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 1. iMessage ──────────────────────────────────────────────────────────────
echo -e "${GREEN}✓ iMessage${NC} — pre-installed via Homebrew (mac-messages-mcp)"
if /opt/homebrew/bin/python3.10 -c "import mac_messages_mcp" 2>/dev/null; then
    echo "  Status: READY"
else
    echo -e "  ${RED}Issue: mac_messages_mcp not importable. Try: brew install mac-messages-mcp${NC}"
fi
echo ""

# ── 2. Playwright ─────────────────────────────────────────────────────────────
echo -e "${GREEN}✓ Playwright${NC} — browser automation (already installed)"
if npx @playwright/mcp --version 2>/dev/null | grep -q "0\."; then
    echo "  Status: READY"
else
    echo "  Installing @playwright/mcp..."
    npm install -g @playwright/mcp 2>/dev/null
fi
echo ""

# ── 3. Perplexity ─────────────────────────────────────────────────────────────
echo -e "${YELLOW}◎ Perplexity${NC} — requires API key"
if grep -q "PERPLEXITY_API_KEY=pplx-" "$ENV_FILE" 2>/dev/null; then
    echo "  Status: READY (key found in ~/.hermes/.env)"
else
    echo ""
    echo "  Get your API key at: https://www.perplexity.ai/settings/api"
    echo -n "  Paste your Perplexity API key (starts with 'pplx-'): "
    read -r PERPLEXITY_KEY
    if [[ "$PERPLEXITY_KEY" == pplx-* ]]; then
        echo "" >> "$ENV_FILE"
        echo "# Perplexity — AI research for agents (Nico, Diane, Mika, Rex)" >> "$ENV_FILE"
        echo "PERPLEXITY_API_KEY=$PERPLEXITY_KEY" >> "$ENV_FILE"
        echo -e "  ${GREEN}✓ Key saved to ~/.hermes/.env${NC}"
    else
        echo -e "  ${RED}Invalid key format. Add manually: echo 'PERPLEXITY_API_KEY=pplx-...' >> ~/.hermes/.env${NC}"
    fi
fi
echo ""

# ── 4. Google Calendar ────────────────────────────────────────────────────────
echo -e "${YELLOW}◎ Google Calendar${NC} — requires OAuth credentials"
if [[ -f "$GOOGLE_DIR/credentials.json" ]]; then
    echo "  Status: credentials.json found"
    if [[ -f "$GOOGLE_DIR/mcp-google-calendar-token.json" ]]; then
        echo -e "  Status: ${GREEN}READY (already authenticated)${NC}"
    else
        echo "  First-run authentication needed. Starting..."
        echo "  (A browser window will open — sign in with your Google account)"
        CREDENTIALS_PATH="$GOOGLE_DIR/credentials.json" mcp-google-calendar &
        MCP_PID=$!
        sleep 5
        kill $MCP_PID 2>/dev/null || true
        echo "  If a browser opened and you authenticated, the token is saved."
    fi
else
    echo ""
    echo "  ── Setup Instructions ──────────────────────────────────────────"
    echo ""
    echo "  1. Go to: https://console.cloud.google.com/"
    echo "  2. Create or select a project → 'AI Record Label'"
    echo "  3. APIs & Services → Library → Enable 'Google Calendar API'"
    echo "  4. APIs & Services → OAuth consent screen:"
    echo "     - Choose 'External' → fill app name (AI Record Label)"
    echo "     - Add scope: calendar.events"
    echo "     - Add your email as test user"
    echo "  5. Credentials → Create Credentials → OAuth Client ID"
    echo "     - Type: Desktop app → Name: 'AI Record Label'"
    echo "  6. Download JSON → save as: $GOOGLE_DIR/credentials.json"
    echo ""
    echo "  Then run this script again to authenticate."
    echo ""
    echo -n "  Press Enter to continue (or set up later)..."
    read -r
fi
echo ""

# ── 5. Summary ────────────────────────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  MCP Integration Status"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

check() {
    if eval "$1" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} $2"
    else
        echo -e "  ${RED}✗${NC} $2 — $3"
    fi
}

/opt/homebrew/bin/python3.10 -c "import mac_messages_mcp" && echo -e "  ${GREEN}✓${NC} iMessage" || echo -e "  ${RED}✗${NC} iMessage — run: brew reinstall mac-messages-mcp"
npx @playwright/mcp --version >/dev/null 2>&1 && echo -e "  ${GREEN}✓${NC} Playwright" || echo -e "  ${RED}✗${NC} Playwright — run: npm install -g @playwright/mcp"
grep -q "PERPLEXITY_API_KEY=pplx-" "$ENV_FILE" 2>/dev/null && echo -e "  ${GREEN}✓${NC} Perplexity" || echo -e "  ${YELLOW}◎${NC} Perplexity — add key to ~/.hermes/.env"
[[ -f "$GOOGLE_DIR/credentials.json" ]] && echo -e "  ${GREEN}✓${NC} Google Calendar (credentials found)" || echo -e "  ${YELLOW}◎${NC} Google Calendar — credentials.json needed"
echo ""
