#!/usr/bin/env bash
# ============================================================================
# AI Record Label — Cross-platform launcher (macOS / Linux)
# ============================================================================
# Starts all services and opens the desktop app.
# Usage: ./scripts/launch.sh
#   --no-app     Skip opening the desktop app (services only)
#   --stop       Stop all services
# ============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Load .env if present (exports WATCH_FOLDER and other vars)
ENV_FILE="$ROOT/.env"
if [[ -f "$ENV_FILE" ]]; then
  # Export all non-comment, non-empty lines (set -a auto-exports everything sourced)
  set -a
  # shellcheck disable=SC1090,SC1091
  source "$ENV_FILE"
  set +a
fi

# Resolve data directory (same logic as Rust and Python)
if [[ -n "${AI_RECORD_LABEL_DATA:-}" ]]; then
  DATA_DIR="$AI_RECORD_LABEL_DATA"
elif [[ "$(uname)" == "Darwin" ]]; then
  DATA_DIR="$HOME/Library/Application Support/ai-record-label"
else
  DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/ai-record-label"
fi
mkdir -p "$DATA_DIR/inbox"

# Find hermes and agent binaries
HERMES_BIN="${HERMES_BIN:-$(command -v hermes 2>/dev/null || echo "$HOME/.local/bin/hermes")}"
export PATH="$HOME/.local/bin:$PATH"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}▸${NC} $1"; }
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
err()  { echo -e "${RED}✗${NC} $1"; }

save_healthy_tunnel_url() {
  local url="$1"
  local url_file="$2"
  local host="${url#https://}"
  local ip=""

  host="${host%%/*}"
  ip="$(dig +short "$host" A | head -1)"
  if [[ -z "$ip" ]]; then
    err "Cloudflare tunnel hostname did not resolve: $host"
    return 1
  fi

  if curl -fsS --resolve "$host:443:$ip" --max-time 8 "$url/health" >/dev/null 2>&1; then
    echo "$url" > "$url_file"
    ok "Tunnel URL: $url"
    return 0
  fi

  err "Cloudflare tunnel URL failed health check: $url/health"
  return 1
}

# ── Stop mode ──────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--stop" ]]; then
  log "Stopping all services..."
  for p in a_and_r manager creative_director bandcamp intake; do
    "$HOME/.local/bin/$p" gateway stop 2>/dev/null && ok "$p stopped" || warn "$p already stopped"
  done
  "$HERMES_BIN" gateway stop 2>/dev/null && ok "default gateway stopped" || warn "default already stopped"

  if [[ -f "$DATA_DIR/.watcher.pid" ]]; then
    kill "$(cat "$DATA_DIR/.watcher.pid")" 2>/dev/null && ok "file watcher stopped" || true
    rm -f "$DATA_DIR/.watcher.pid"
  fi
  if [[ "$(uname)" == "Darwin" ]]; then
    launchctl bootout "gui/$(id -u)/com.ai-record-label.watcher" 2>/dev/null || true
  fi
  if [[ -f "$DATA_DIR/.api.pid" ]]; then
    kill "$(cat "$DATA_DIR/.api.pid")" 2>/dev/null && ok "HTTP API server stopped" || true
    rm -f "$DATA_DIR/.api.pid"
  fi
  if [[ -f "$DATA_DIR/.litestream.pid" ]]; then
    kill "$(cat "$DATA_DIR/.litestream.pid")" 2>/dev/null && ok "litestream replication stopped" || true
    rm -f "$DATA_DIR/.litestream.pid"
  fi
  # Cloudflare tunnel is managed by LaunchAgent — leave it running (auto-restarts)
  # To fully stop it: launchctl unload ~/Library/LaunchAgents/com.ai-record-label.cloudflared.plist
  if [[ -f "$DATA_DIR/.cloudflared.pid" ]]; then
    kill "$(cat "$DATA_DIR/.cloudflared.pid")" 2>/dev/null && ok "cloudflare tunnel (fallback) stopped" || true
    rm -f "$DATA_DIR/.cloudflared.pid"
  fi
  warn "cloudflare tunnel (LaunchAgent) kept alive — manages itself on boot/crash"
  ok "All services stopped."
  exit 0
fi

echo ""
echo -e "${CYAN}╔══════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     🎵  AI Record Label  🎵         ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════╝${NC}"
echo ""
log "Data dir: $DATA_DIR"

# ── 1. Check prerequisites ────────────────────────────────────────────────
log "Checking prerequisites..."
if ! command -v hermes &>/dev/null && [[ ! -x "$HERMES_BIN" ]]; then
  err "hermes not found. Install from: https://github.com/NousResearch/hermes-agent"
  exit 1
fi

VENV_PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
  err "Project virtualenv Python not found at $VENV_PYTHON"
  exit 1
fi

# ── 2. Initialize DB and apply all migrations ─────────────────────────────
DB_FILE="$DATA_DIR/hermes.db"
log "Applying database migrations..."
if ! command -v sqlite3 &>/dev/null; then
  err "sqlite3 not found; database migrations cannot run"
  exit 1
fi

MIGRATE_SCRIPT="$ROOT/hermes-config/scripts/migrate_db.sh"
if [[ ! -x "$MIGRATE_SCRIPT" ]]; then
  err "Migration script not executable: $MIGRATE_SCRIPT"
  exit 1
fi
bash "$MIGRATE_SCRIPT" "$DB_FILE" 2>&1 | while IFS= read -r line; do
  [[ -n "$line" ]] && log "$line"
done
ok "Database ready: $DB_FILE"

# ── 2b. Litestream replication (Mac → B2) ────────────────────────────────
# Streams SQLite WAL changes to Backblaze B2 in real-time.
# Render restores from B2 on startup → dashboard stays in sync even when Mac is off.
LS_BIN="${LITESTREAM_BIN:-$(command -v litestream 2>/dev/null || echo "")}"
LS_CFG="$ROOT/litestream.yml"
LS_PID_FILE="$DATA_DIR/.litestream.pid"

if [[ -z "$LS_BIN" ]]; then
  warn "litestream not found — DB won't replicate to cloud. Install: brew install litestream"
elif [[ ! -f "$LS_CFG" ]]; then
  warn "litestream.yml not found at $LS_CFG — replication skipped"
elif [[ -z "${B2_WRITE_KEY_ID:-}" || -z "${B2_WRITE_APPLICATION_KEY:-}" ]]; then
  warn "B2_WRITE_KEY_ID / B2_WRITE_APPLICATION_KEY not set — replication skipped"
  warn "  Set these in $ENV_FILE to enable cloud sync"
else
  if [[ -f "$LS_PID_FILE" ]] && kill -0 "$(cat "$LS_PID_FILE")" 2>/dev/null; then
    ok "Litestream already replicating (PID $(cat "$LS_PID_FILE"))"
  else
    AI_RECORD_LABEL_DATA="$DATA_DIR" \
      B2_ENDPOINT_URL="${B2_ENDPOINT_URL:-https://s3.us-east-005.backblazeb2.com}" \
      B2_BUCKET_NAME="${B2_BUCKET_NAME:-ai-record-label-vault}" \
      B2_WRITE_KEY_ID="$B2_WRITE_KEY_ID" \
      B2_WRITE_APPLICATION_KEY="$B2_WRITE_APPLICATION_KEY" \
      nohup "$LS_BIN" replicate -config "$LS_CFG" \
        >> "$DATA_DIR/litestream.log" 2>&1 &
    echo $! > "$LS_PID_FILE"
    ok "Litestream replication started (PID $(cat "$LS_PID_FILE"))"
    log "  DB → B2 bucket: ${B2_BUCKET_NAME:-ai-record-label-vault}/litestream/hermes.db"
    log "  Sync interval: 10s  |  Retention: 24h"
    log "  Log: $DATA_DIR/litestream.log"
  fi
fi

# ── 2.5 Required key + model rundown ──────────────────────────────────────
if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  err "OPENROUTER_API_KEY is not set — every agent and pipeline stage needs it."
  err "Get a key at https://openrouter.ai/keys, add it to $ROOT/.env, and relaunch."
  exit 1
fi
uv run --project "$ROOT" python "$ROOT/scripts/model_rundown.py"

# ── 3. Start gateways ─────────────────────────────────────────────────────
log "Starting agent gateways..."
"$HERMES_BIN" gateway start 2>/dev/null || true
for p in a_and_r manager creative_director bandcamp intake; do
  if command -v "$p" &>/dev/null; then
    "$p" gateway start 2>/dev/null && ok "$p gateway" || warn "$p gateway already running"
  else
    warn "$p agent not installed — skipping"
  fi
done

# ── 4. Start file watcher ─────────────────────────────────────────────────
INBOX_DIR="$DATA_DIR/inbox"

# Read Ableton folders from saved settings if present
ABLETON_EXPORT=""
ABLETON_PROJECT=""
SETTINGS_FILE="$DATA_DIR/settings.json"
if [[ -f "$SETTINGS_FILE" ]]; then
  ABLETON_EXPORT=$(SETTINGS_FILE="$SETTINGS_FILE" "$VENV_PYTHON" -c 'import json, os; d=json.load(open(os.environ["SETTINGS_FILE"])); print(d.get("ableton_export_folder", ""))')
  ABLETON_PROJECT=$(SETTINGS_FILE="$SETTINGS_FILE" "$VENV_PYTHON" -c 'import json, os; d=json.load(open(os.environ["SETTINGS_FILE"])); print(d.get("ableton_project_folder", ""))')
fi

# Determine which folder to watch:
#   1. WATCH_FOLDER env var (set in .env — Google Drive path)
#   2. ableton_export_folder from settings.json
#   3. default inbox
WATCH_DIR="${WATCH_FOLDER:-${ABLETON_EXPORT:-$INBOX_DIR}}"
[[ -n "$WATCH_DIR" ]] && mkdir -p "$WATCH_DIR"

log "Starting file watcher on ${WATCH_DIR}..."
[[ -n "$ABLETON_PROJECT" ]] && log "Ableton project: $ABLETON_PROJECT"

# Google Drive inbox — auto-detect from mounted CloudStorage (any account)
GDRIVE_BASE=$(ls -d "$HOME/Library/CloudStorage/GoogleDrive-"* 2>/dev/null | head -1)
if [[ -n "$GDRIVE_BASE" ]]; then
  GDRIVE_INBOX="$GDRIVE_BASE/My Drive/AI Record Label/inbox"
  mkdir -p "$GDRIVE_INBOX" 2>/dev/null || true
else
  err "Google Drive CloudStorage folder not found; export sync destination cannot be verified"
  exit 1
fi

WATCHER_LABEL="com.ai-record-label.watcher"
WATCHER_PLIST="$HOME/Library/LaunchAgents/${WATCHER_LABEL}.plist"
WATCHER_WRAPPER="$DATA_DIR/watcher-wrapper.sh"

WATCHER_PID=""
if [[ "$(uname)" == "Darwin" ]] && command -v launchctl >/dev/null 2>&1; then
  WATCHER_PID=$(launchctl list 2>/dev/null | awk -v label="$WATCHER_LABEL" '$3 == label {print $1; exit}')
fi

if [[ -z "$WATCHER_PID" && -f "$DATA_DIR/.watcher.pid" ]] && kill -0 "$(cat "$DATA_DIR/.watcher.pid")" 2>/dev/null; then
  WATCHER_PID="$(cat "$DATA_DIR/.watcher.pid")"
fi

if [[ -n "$WATCHER_PID" && "$WATCHER_PID" != "-" ]]; then
  echo "$WATCHER_PID" > "$DATA_DIR/.watcher.pid"
  ok "File watcher already running (PID $WATCHER_PID)"
else
  cd "$ROOT"
  if [[ "$(uname)" == "Darwin" ]] && command -v launchctl >/dev/null 2>&1; then
    cat > "$WATCHER_WRAPPER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$ROOT"
export PATH="/opt/homebrew/bin:$HOME/.local/bin:\$PATH"
export AI_RECORD_LABEL_DATA="$DATA_DIR"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi
args=("$VENV_PYTHON" -m file_watcher.watcher "$WATCH_DIR" "$DB_FILE")
if [[ -n "$ABLETON_PROJECT" ]]; then
  args+=("$ABLETON_PROJECT")
fi
args+=(--sync-dest "$GDRIVE_INBOX" --b2-sync-script "$ROOT/scripts/sync_to_cloud.sh")
exec "\${args[@]}"
EOF
    chmod +x "$WATCHER_WRAPPER"

    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$WATCHER_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$WATCHER_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$WATCHER_WRAPPER</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>ThrottleInterval</key>
  <integer>5</integer>
  <key>StandardOutPath</key>
  <string>$DATA_DIR/watcher.log</string>
  <key>StandardErrorPath</key>
  <string>$DATA_DIR/watcher.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>$HOME</string>
    <key>PATH</key>
    <string>/opt/homebrew/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>AI_RECORD_LABEL_DATA</key>
    <string>$DATA_DIR</string>
  </dict>
</dict>
</plist>
EOF
    plutil -lint "$WATCHER_PLIST" >/dev/null
    launchctl bootout "gui/$(id -u)/$WATCHER_LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$WATCHER_PLIST"
    launchctl enable "gui/$(id -u)/$WATCHER_LABEL" 2>/dev/null || true
    launchctl kickstart -k "gui/$(id -u)/$WATCHER_LABEL"
    for _ in 1 2 3 4 5; do
      sleep 1
      WATCHER_PID=$(launchctl list 2>/dev/null | awk -v label="$WATCHER_LABEL" '$3 == label {print $1; exit}')
      if [[ -n "$WATCHER_PID" && "$WATCHER_PID" != "-" ]]; then
        break
      fi
    done
    if [[ -z "$WATCHER_PID" || "$WATCHER_PID" == "-" ]]; then
      err "File watcher LaunchAgent failed to start — check $DATA_DIR/watcher.log"
      exit 1
    fi
    echo "$WATCHER_PID" > "$DATA_DIR/.watcher.pid"
  else
    # Args: watch_dir db_path [project_folder] [--sync-dest dir] [--b2-sync-script path]
    # /opt/homebrew/bin added so fpcalc (chromaprint) is found for fingerprinting
    PATH="/opt/homebrew/bin:$PATH" AI_RECORD_LABEL_DATA="$DATA_DIR" \
      nohup \
      "$VENV_PYTHON" -m file_watcher.watcher \
        "$WATCH_DIR" "$DB_FILE" "$ABLETON_PROJECT" \
        --sync-dest "$GDRIVE_INBOX" \
        --b2-sync-script "$ROOT/scripts/sync_to_cloud.sh" \
        >> "$DATA_DIR/watcher.log" 2>&1 &
    echo $! > "$DATA_DIR/.watcher.pid"
  fi
  ok "File watcher (PID $(cat "$DATA_DIR/.watcher.pid"))"
  log "  Watching: $WATCH_DIR"
  log "  → Google Drive: $GDRIVE_INBOX"
  log "  → B2 vault (on each new file)"
fi

# ── 4b. Build web frontend if needed ─────────────────────────────────────
DIST_DIR="$ROOT/desktop-app/dist"
if [[ ! -d "$DIST_DIR" ]]; then
  log "Building web frontend..."
  cd "$ROOT/desktop-app"
  if command -v npm &>/dev/null; then
    npm run build 2>&1 | tail -5
    ok "Web frontend built → $DIST_DIR"
  else
    err "npm not found; web UI cannot be built"
    exit 1
  fi
  cd "$ROOT"
else
  ok "Web frontend (pre-built)"
fi

# ── 5. HTTP API server (port 8086 — what the Cloudflare tunnel proxies) ──
# Note: port 8085 is reserved for Hermes studio gateway internal use.
API_PID_FILE="$DATA_DIR/.api.pid"
API_LISTEN_PID="$(lsof -tiTCP:8086 -sTCP:LISTEN 2>/dev/null | head -1 || true)"
if curl -sf http://localhost:8086/health >/dev/null 2>&1; then
  if [[ -n "$API_LISTEN_PID" ]]; then
    echo "$API_LISTEN_PID" > "$API_PID_FILE"
    ok "HTTP API server already running (PID $API_LISTEN_PID) → http://localhost:8086"
  else
    err "HTTP API /health passed but no listening PID was found on port 8086"
    exit 1
  fi
else
  VENV_PYTHON_API="$VENV_PYTHON"
  API_PORT=8086 AI_RECORD_LABEL_DATA="$DATA_DIR" \
    nohup \
    "$VENV_PYTHON_API" "$ROOT/http_api.py" >> "$DATA_DIR/api.log" 2>&1 &
  echo $! > "$API_PID_FILE"
  # Wait up to 5s for the API to start (retry loop beats fixed sleep)
  API_READY=0
  for i in 1 2 3 4 5; do
    sleep 1
    if kill -0 "$(cat "$API_PID_FILE")" 2>/dev/null && curl -sf http://localhost:8086/health >/dev/null 2>&1; then
      ok "HTTP API server (PID $(cat "$API_PID_FILE")) → http://localhost:8086"
      API_READY=1
      break
    fi
  done
  if [[ "$API_READY" -ne 1 ]]; then
    err "HTTP API server failed to start — check $DATA_DIR/api.log"
    kill "$(cat "$API_PID_FILE")" 2>/dev/null || true
    rm -f "$API_PID_FILE"
    exit 1
  fi
fi

# ── 6. Cloudflare tunnel ──────────────────────────────────────────────────
CLOUDFLARED="${CLOUDFLARED_BIN:-$(command -v cloudflared 2>/dev/null || echo "/opt/homebrew/bin/cloudflared")}"
CF_URL_FILE="$DATA_DIR/.cloudflared.url"
CF_LAUNCHAGENT="com.ai-record-label.cloudflared"
CF_PLIST="$HOME/Library/LaunchAgents/${CF_LAUNCHAGENT}.plist"

if [[ -x "$CLOUDFLARED" ]]; then
  # Check if the LaunchAgent is managing cloudflared (preferred — auto-restarts on crash/boot)
  LAUNCHCTL_SERVICES="$(launchctl list 2>/dev/null || true)"
  if grep -q "$CF_LAUNCHAGENT" <<< "$LAUNCHCTL_SERVICES"; then
    CF_PID=$(awk -v label="$CF_LAUNCHAGENT" '$3 == label {print $1}' <<< "$LAUNCHCTL_SERVICES")
    ok "Cloudflare tunnel managed by LaunchAgent (PID ${CF_PID})"
    if [[ -s "$CF_URL_FILE" ]]; then
      save_healthy_tunnel_url "$(cat "$CF_URL_FILE")" "$CF_URL_FILE" || exit 1
    else
      CF_URL=""
      for i in 1 2 3 4 5; do
        sleep 2
        CF_URL=$(grep -o "https://[a-z0-9-]*\.trycloudflare\.com" "$DATA_DIR/cloudflared.log" 2>/dev/null | tail -1 || true)
        if [[ -n "$CF_URL" ]] && save_healthy_tunnel_url "$CF_URL" "$CF_URL_FILE"; then
          break
        fi
      done
      if [[ ! -s "$CF_URL_FILE" ]]; then
        err "Cloudflare LaunchAgent did not produce a healthy tunnel URL — check $DATA_DIR/cloudflared.log"
        exit 1
      fi
    fi
  else
    log "Starting Cloudflare tunnel → http://127.0.0.1:8086"
    > "$DATA_DIR/cloudflared.log"
    nohup "$CLOUDFLARED" tunnel --url http://127.0.0.1:8086 \
      >> "$DATA_DIR/cloudflared.log" 2>&1 &
    CF_PID=$!
    echo $CF_PID > "$DATA_DIR/.cloudflared.pid"
    ok "Quick tunnel started (PID $CF_PID)"
    CF_URL=""
    for i in $(seq 1 30); do
      sleep 2
      CF_URL=$(grep -o "https://[a-z0-9-]*\.trycloudflare\.com" "$DATA_DIR/cloudflared.log" 2>/dev/null | tail -1 || true)
      if [[ -n "$CF_URL" ]] && save_healthy_tunnel_url "$CF_URL" "$CF_URL_FILE"; then
        break
      fi
    done
    if [[ ! -s "$CF_URL_FILE" ]]; then
      err "Cloudflare tunnel did not pass /health after 60s — check $DATA_DIR/cloudflared.log"
      kill "$CF_PID" 2>/dev/null || true
      rm -f "$DATA_DIR/.cloudflared.pid"
      exit 1
    fi
  fi
else
  err "cloudflared not found. Install: brew install cloudflared"
  exit 1
fi

# ── 6. Verify connectivity ────────────────────────────────────────────────
log "Verifying gateways..."
sleep 1
RUNNING=$("$HERMES_BIN" gateway list 2>&1 | grep -c "✓" || true)
echo -e "  ${GREEN}${RUNNING}${NC} gateways running"

# ── 7. Web app URL ───────────────────────────────────────────────────────
if [[ "${1:-}" != "--no-app" ]]; then
  WEB_URL="http://localhost:8086"
  ok "Web app → $WEB_URL"
  if [[ -f "$DATA_DIR/.cloudflared.url" ]]; then
    ok "Remote  → $(cat "$DATA_DIR/.cloudflared.url")"
  fi
  # Open in default browser on macOS
  if [[ "$(uname)" == "Darwin" ]]; then
    open "$WEB_URL" 2>/dev/null || true
  fi
fi

echo ""
ok "AI Record Label is running!"
echo ""
echo "  Data:      $DATA_DIR"
echo "  Database:  $DB_FILE"
echo "  Inbox:     $WATCH_DIR"
echo "  Logs:      $DATA_DIR/*.log"
if [[ -f "$DATA_DIR/.litestream.pid" ]] && kill -0 "$(cat "$DATA_DIR/.litestream.pid")" 2>/dev/null; then
echo "  DB Sync:   Litestream → B2 (live) [PID $(cat "$DATA_DIR/.litestream.pid")]"
else
echo "  DB Sync:   offline (set B2_WRITE_KEY_ID + B2_WRITE_APPLICATION_KEY to enable)"
fi
if [[ -f "$DATA_DIR/.cloudflared.url" ]]; then
echo "  Tunnel:    $(cat "$DATA_DIR/.cloudflared.url")"
fi
echo ""
echo "  Stop all:  $0 --stop"
echo ""
