# NEXT — Pre-Deploy Checklist & Roadmap

Things left to do before this is fully live, plus the deployment plan.
Add to this as new items come up.

---

## In Progress / To Add Before Deploy

- [x] Wire MCP integrations (Google Calendar, iMessage, Browser/Playwright, Perplexity) to agent profiles — all 5 profiles done; Gmail deferred (no MCP server)
- [x] Build Tier 0/1 MCP tool definitions (16 tools added to mcp_server.py)
- [x] Build Tier 0/1 MCP tool handler implementations (all 16 handlers written and tested)
- [x] Apply schema migrations for all new tables (004_memory.sql + 005_features_tier0_tier1.sql)
- [x] Update all agent SOUL.md files with new behaviors (vault, panel, release cycle, work patterns, royalty, album-as-statement)
- [x] Update file watcher to create calendar events on export + session clustering
- [x] Write 72-test suite (tests/*) — 288 pass, 0 fail
- [x] Implement agent cron schedule — 13 jobs live in Hermes (see AGENT_SCHEDULE.md)
- [ ] Build Tier 2 features (see FEATURES.md)
- [x] Test end-to-end: drop a WAV → file watcher → DB registration → fingerprint → export_event → Google Calendar event (live-verified May 2026)
- [ ] Full release cycle e2e: feedback → panel → approved → art → release_ready → preflight → Bandcamp upload

---

## Phase 1 — Mac + Cloudflare Tunnel ✅ DONE

Goal: agents run on your Mac 24/7, accessible from any device via Twilio SMS (already works) and optionally via a secure tunnel for dashboard access.

- [x] Install `cloudflared` — `brew install cloudflared`
- [x] Route studio gateway (port 8085) through the tunnel
- [x] Add tunnel to `scripts/launch.sh` so it starts with everything else
- [x] Add tunnel to macOS LaunchAgent — auto-starts on login, auto-restarts on crash
- [x] URL saved to `$DATA_DIR/.cloudflared.url` after each start
- [x] Google Calendar integration — exports fire calendar events automatically
- [ ] Keep Mac plugged in and Sleep → Never for display + disk (System Settings → Battery)

**Note:** Using quick tunnels (trycloudflare.com) — URL changes on restart but tunnel
auto-recovers. Named tunnel (stable URL) requires a domain in Cloudflare.
See `OPERATIONS.md` for full runbook.

**Cost:** Free  
**Status:** Running — check current URL with `cat ~/Library/"Application Support"/ai-record-label/.cloudflared.url`

---

## Phase 2 — Hetzner CAX11 VPS (~$5/month, do when ready)

Goal: agents run on a dedicated ARM64 server 24/7 regardless of whether the Mac is on. Mac becomes dev/build machine only.

- [ ] Create Hetzner account at hetzner.com
- [ ] Provision CAX11 (2 vCPU ARM64, 4GB RAM, 40GB SSD, Ashburn or Helsinki)
- [ ] SSH key setup, initial Ubuntu/Debian config
- [ ] Install Python 3.12, uv, Hermes on VPS
- [ ] Sync `~/.hermes/profiles/` to VPS (rsync or git-managed)
- [ ] Sync `hermes.db` + set up DB path env var on VPS
- [ ] Set up Cloudflare Tunnel on VPS (replace Mac tunnel)
- [ ] Move Twilio webhook endpoints to point at VPS
- [ ] Set up systemd service for Hermes + file watcher (auto-restart on crash)
- [ ] Test full end-to-end: send SMS → studio conductor → agent → reply
- [ ] Point desktop app `DB_PATH` at VPS DB (or set up read replica)
- [ ] Set up daily DB backup to Hetzner Object Storage or Backblaze B2 (~$0.006/GB)

**Cost:** ~$5/month for the VPS  
**Time to set up:** ~2-3 hours  
**Prerequisites:** Phase 1 working first

---

## Notes

- Twilio SMS already works from anywhere — that's the primary mobile interface and requires no tunnel
- The desktop app (Tauri) talks directly to the local/VPS DB via `AI_RECORD_LABEL_DATA` env var
- SQLite is single-writer safe as long as only one Hermes process writes at a time — this is the architecture we have
- Don't run Hermes on both Mac and VPS simultaneously pointing at the same DB — pick one as the primary
