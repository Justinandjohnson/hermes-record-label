#!/usr/bin/env bash
# =============================================================================
# One-time setup: Create a permanent Cloudflare named tunnel
# =============================================================================
# Run this ONCE. After setup, launch.sh uses the permanent tunnel automatically.
#
# Prerequisites:
#   1. brew install cloudflared
#   2. cloudflared login   (opens browser — log in to Cloudflare)
#   3. bash scripts/setup_cloudflare_tunnel.sh
#
# What this does:
#   - Creates a named tunnel "ai-record-label" in your Cloudflare account
#   - Registers a free *.cfargotunnel.com subdomain (or your own domain)
#   - Writes tunnel credentials to ~/.cloudflared/ai-record-label.json
#   - Creates a LaunchAgent so the tunnel starts on Mac boot automatically
# =============================================================================
set -euo pipefail

TUNNEL_NAME="ai-record-label"
DATA_DIR="${AI_RECORD_LABEL_DATA:-$HOME/Library/Application Support/ai-record-label}"
CF_DIR="$HOME/.cloudflared"
PLIST_PATH="$HOME/Library/LaunchAgents/com.ai-record-label.cloudflared.plist"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${CYAN}▸${NC} $1"; }
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }

# ── 1. Check prerequisites ────────────────────────────────────────────────
if ! command -v cloudflared &>/dev/null; then
  echo "cloudflared not found. Install: brew install cloudflared"
  exit 1
fi

if [[ ! -f "$CF_DIR/cert.pem" ]]; then
  warn "Not logged in to Cloudflare. Running: cloudflared login"
  cloudflared login
fi

# ── 2. Create named tunnel ────────────────────────────────────────────────
log "Creating named tunnel '$TUNNEL_NAME'..."
if cloudflared tunnel list 2>/dev/null | grep -q "$TUNNEL_NAME"; then
  ok "Tunnel '$TUNNEL_NAME' already exists"
  TUNNEL_ID=$(cloudflared tunnel list 2>/dev/null | grep "$TUNNEL_NAME" | awk '{print $1}')
else
  cloudflared tunnel create "$TUNNEL_NAME"
  TUNNEL_ID=$(cloudflared tunnel list 2>/dev/null | grep "$TUNNEL_NAME" | awk '{print $1}')
  ok "Tunnel created. ID: $TUNNEL_ID"
fi

# ── 3. Write tunnel config ────────────────────────────────────────────────
CONFIG_FILE="$CF_DIR/config.yml"
log "Writing tunnel config to $CONFIG_FILE..."
cat > "$CONFIG_FILE" << EOF
tunnel: $TUNNEL_NAME
credentials-file: $CF_DIR/$TUNNEL_ID.json

ingress:
  - hostname: $TUNNEL_NAME.cfargotunnel.com
    service: http://localhost:8086
  - service: http_status:404
EOF
ok "Tunnel config written"

# ── 4. Route DNS ──────────────────────────────────────────────────────────
log "Creating DNS route for $TUNNEL_NAME.cfargotunnel.com..."
cloudflared tunnel route dns "$TUNNEL_NAME" "$TUNNEL_NAME.cfargotunnel.com" 2>/dev/null || true
ok "DNS route ready"

PUBLIC_URL="https://$TUNNEL_NAME.cfargotunnel.com"

# ── 5. Save URL for launch.sh ─────────────────────────────────────────────
mkdir -p "$DATA_DIR"
echo "$PUBLIC_URL" > "$DATA_DIR/.cloudflared.url"
ok "URL saved: $PUBLIC_URL"

# ── 6. Install LaunchAgent (auto-start on Mac boot) ──────────────────────
log "Installing LaunchAgent for auto-start on boot..."
cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.ai-record-label.cloudflared</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(command -v cloudflared)</string>
    <string>tunnel</string>
    <string>--config</string>
    <string>$CONFIG_FILE</string>
    <string>run</string>
    <string>$TUNNEL_NAME</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$DATA_DIR/cloudflared.log</string>
  <key>StandardErrorPath</key>
  <string>$DATA_DIR/cloudflared.log</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load -w "$PLIST_PATH"
ok "LaunchAgent installed and started"

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Permanent tunnel ready!                              ║${NC}"
echo -e "${GREEN}║                                                       ║${NC}"
echo -e "${GREEN}║  URL: $PUBLIC_URL  ║${NC}"
echo -e "${GREEN}║                                                       ║${NC}"
echo -e "${GREEN}║  This URL never changes. Works from any device.      ║${NC}"
echo -e "${GREEN}║  Tunnel auto-starts when Mac boots.                  ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  To stop:   launchctl unload $PLIST_PATH"
echo "  To restart: launchctl kickstart -k gui/\$(id -u)/com.ai-record-label.cloudflared"
