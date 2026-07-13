# AI Record Label — Operations Runbook

Everything you need to know to run, debug, and recover the system.
Last updated: 2026-05-15

---

## Quick Start

```bash
cd ~/gaer/ai-record-label
./scripts/launch.sh          # start everything
./scripts/launch.sh --stop   # stop agents + file watcher (tunnel stays up)
```

---

## What Runs Where

| Service | How it runs | Restart on crash? |
|---------|------------|-------------------|
| Hermes + agent gateways | `launch.sh` → `hermes gateway start` | No — rerun launch.sh |
| File watcher | `launch.sh` → background PID in `$DATA_DIR/.watcher.pid` | No — rerun launch.sh |
| Cloudflare tunnel | macOS LaunchAgent (permanent) | **Yes — auto** |
| Desktop app (Tauri) | `launch.sh` → opens bundle or Vite dev server | No |

---

## Data Directory

```
~/Library/Application Support/ai-record-label/
├── hermes.db              # main SQLite database
├── inbox/                 # file watcher drop folder
├── .cloudflared.url       # current public tunnel URL (auto-updated)
├── cloudflared.log        # cloudflared output (rotated on each restart)
├── .watcher.pid           # file watcher PID
└── settings.json          # optional: ableton_export_folder, ableton_project_folder
```

---

## Cloudflare Tunnel (permanent, auto-restarting)

### How it works

A macOS **LaunchAgent** runs `cloudflared-wrapper.sh` on login and auto-restarts it on crash.
The wrapper starts a Cloudflare **quick tunnel** pointing at `http://localhost:8085`, rotates
the log, then extracts and saves the public URL to `.cloudflared.url`.

Because it's a quick tunnel (no registered domain needed), **the subdomain changes on each
restart**. The tunnel itself comes back automatically — you never have to touch it.

### Current setup files

```
~/Library/LaunchAgents/com.ai-record-label.cloudflared.plist
~/Library/Application Support/ai-record-label/cloudflared-wrapper.sh
```

### Get the current public URL

```bash
cat ~/Library/"Application Support"/ai-record-label/.cloudflared.url
# e.g. https://manitoba-york-expansion-happens.trycloudflare.com
```

### Check tunnel health

```bash
curl "$(cat ~/Library/"Application Support"/ai-record-label/.cloudflared.url)/health"
# should return: ok
```

### Check LaunchAgent status

```bash
launchctl list | grep cloudflared
# PID  0  com.ai-record-label.cloudflared  ← state=running means healthy
```

### Stop/start the tunnel manually

```bash
# Stop
launchctl unload ~/Library/LaunchAgents/com.ai-record-label.cloudflared.plist

# Start
launchctl load -w ~/Library/LaunchAgents/com.ai-record-label.cloudflared.plist

# Restart (e.g. to force a new URL)
launchctl kickstart -k gui/$(id -u)/com.ai-record-label.cloudflared
```

### If the URL file is stale / health check fails

```bash
# Get the real current URL from the log
grep -o "https://[a-z0-9-]*\.trycloudflare\.com" \
  ~/Library/"Application Support"/ai-record-label/cloudflared.log | tail -1

# Update the file
grep -o "https://[a-z0-9-]*\.trycloudflare\.com" \
  ~/Library/"Application Support"/ai-record-label/cloudflared.log | tail -1 \
  > ~/Library/"Application Support"/ai-record-label/.cloudflared.url
```

### Known limitation — URL changes on restart

Quick tunnels (trycloudflare.com) always get a new subdomain when restarted.
To get a **stable permanent URL**, you'd need either:
- A domain registered in Cloudflare → run `cloudflared tunnel login` + `tunnel create`
- Or upgrade to a paid Cloudflare plan

For now the tunnel comes back automatically; you just have to check `.cloudflared.url`
for the new address after a reboot.

---

## Google Calendar Integration

Audio exports detected by the file watcher automatically create Google Calendar events.

### OAuth credentials

| File | Purpose |
|------|---------|
| `~/.hermes/google/credentials.json` | OAuth client (Desktop app, "AI Record Label" project in GCP) |
| `~/.hermes/google/mcp-google-calendar-token.json` | Access + refresh token for your Google account |

**Account:** your Google account (set up during OAuth flow)  
**GCP Project:** your GCP project (create one at console.cloud.google.com)  
**OAuth Client ID:** from your GCP project's OAuth credentials (Desktop app type)  
**Calendar:** primary (the account's default calendar)  

### How it works

`session_intelligence/calendar_sync.py` → `create_export_event()`:
1. Loads `credentials.json` + token from `~/.hermes/google/`
2. Auto-refreshes the access token if expired (saves refreshed token back to disk)
3. Creates a sage-green (colorId "2") 30-minute event with:
   - Summary: `🎛 Export · {project_name} · Δ v{version}`
   - Description: file name, BPM, similarity score, session date, full path

### If calendar events stop appearing

```bash
# Test the connection directly
cd ~/gaer/ai-record-label
.venv/bin/python - <<'EOF'
from session_intelligence.calendar_sync import create_export_event
from pathlib import Path
link = create_export_event(
    project_name="Test",
    file_path=Path("/tmp/test.wav"),
    bpm=90,
    version=1,
    changed=True,
)
print("Event link:", link)
EOF
```

If it fails with a token error, re-authenticate:
```bash
cd ~/.hermes/google
CREDENTIALS_PATH="$HOME/.hermes/google/credentials.json" mcp-google-calendar
# Follow the browser OAuth flow → token saved automatically
```

---

## File Watcher → Session Intelligence Pipeline

When an audio file lands in the watched folder, `SessionIntelligenceEmitter` runs 5 steps:

1. **Change detection** — compares fingerprint/spectrogram against previous export
2. **Metadata tagging** — writes project name, BPM, session date into the audio file's ID3/FLAC tags
3. **Session linking** — finds the nearest Ableton session in the DB by timestamp
4. **Export count bump** — increments `ableton_sessions.export_count`
5. **Calendar event** — creates a Google Calendar event (see above)

### Configure watched folders

Edit `~/Library/Application Support/ai-record-label/settings.json`:
```json
{
  "ableton_export_folder": "/path/to/your/Ableton/Exports",
  "ableton_project_folder": "/path/to/your/Ableton Project"
}
```

Then restart the file watcher:
```bash
./scripts/launch.sh --stop && ./scripts/launch.sh --no-app
```

### Default inbox (no settings.json)

Drop any `.wav`, `.mp3`, `.aiff`, or `.flac` file into:
```
~/Library/Application Support/ai-record-label/inbox/
```

---

## MCP Server

The Hermes MCP server runs on port 8085 and is what the Cloudflare tunnel exposes.

```bash
# Check it's running
curl http://localhost:8085/health

# View registered tools
curl http://localhost:8085/tools | python3 -m json.tool | head -40
```

Agent profiles that use mcp-google-calendar have `CREDENTIALS_PATH` set in their
Hermes config (`~/.hermes/profiles/studio/config.yaml`, etc.).

---

## Agent Profiles

```
~/.hermes/profiles/
├── a_and_r/       # A&R — discovers and signs talent
├── manager/       # Artist manager — coordinates releases
├── creative_director/  # Creative direction, artwork
├── bandcamp/      # Bandcamp publishing agent
└── studio/        # Studio conductor — routes audio/session work
```

Each profile has `config.yaml` (MCP tools, env vars) and `SOUL.md` (personality/behavior).

---

## Database

SQLite at `~/Library/Application Support/ai-record-label/hermes.db`.

WAL mode enabled. Key tables:
- `ableton_sessions` — session metadata (project, BPM, started_at, export_count)
- `export_events` — per-export fingerprint + similarity score
- `tracks` — registered audio files

```bash
# Quick inspection
sqlite3 ~/Library/"Application Support"/ai-record-label/hermes.db \
  "SELECT project_name, bpm, export_count FROM ableton_sessions ORDER BY started_at DESC LIMIT 10;"
```

---

## Logs

| Log | Location |
|-----|---------|
| Cloudflare tunnel | `$DATA_DIR/cloudflared.log` |
| File watcher | stderr (stdout of launch.sh) |
| Hermes agents | `~/.hermes/logs/` |
| Session intelligence | Python logging → stderr of file watcher process |

---

## Common Recovery Steps

### "The tunnel URL isn't working"
```bash
# Get the real current URL
grep -o "https://[a-z0-9-]*\.trycloudflare\.com" \
  ~/Library/"Application Support"/ai-record-label/cloudflared.log | tail -1
```

### "File watcher isn't picking up tracks"
```bash
# Check if it's running
cat ~/Library/"Application Support"/ai-record-label/.watcher.pid
kill -0 $(cat ~/Library/"Application Support"/ai-record-label/.watcher.pid) && echo "running" || echo "dead"
# Restart
./scripts/launch.sh --stop && ./scripts/launch.sh
```

### "Calendar events stopped"
Re-run the mcp-google-calendar auth flow (token may have expired):
```bash
cd ~/.hermes/google
CREDENTIALS_PATH="$HOME/.hermes/google/credentials.json" mcp-google-calendar
```

### "Agents aren't responding to SMS"
```bash
# Check Hermes gateways
hermes gateway list
# Restart everything
./scripts/launch.sh --stop && ./scripts/launch.sh
```
