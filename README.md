# Hermes — an AI record label that runs itself

A multi-agent system that operates a working record label for an independent artist: A&R, release management, sync licensing, creative direction, artwork, and artist communication — all run by autonomous agents coordinating over a shared database, reachable by SMS.

Built from first-principles study of how 19 labels (Motown through Brainfeeder) and artists like Quincy Jones, Prince, and D'Angelo actually operated — encoded into agent behaviors.

> **Status:** live and running daily for one artist (me). 288 tests passing. Tier 0/1 features complete; full release-cycle automation in progress. See [NEXT.md](NEXT.md).

## The label staff

| Agent | Role |
|---|---|
| **Studio** | Conductor — routes work between agents, talks to the artist over SMS/iMessage |
| **Diane** | Label manager — release cycles, deadlines, calendar milestones, press outreach drafts |
| **Nico** | Creative director — reference research, production feedback, artwork direction |
| **Rex** | Sync licensing — pitch drafts to sync libraries with track metadata |
| **Bandcamp** | Storefront — page management, upload preflight, Bandcamp Friday scheduling |

Every outbound email or upload goes through draft → artist approval → send. Agents never publish on their own.

## What it does

- **Listens to your exports** — a file watcher catches every bounce from your DAW, fingerprints it, registers it in the catalog, and drops a session event on your Google Calendar
- **Hears the music** — audio analysis pipeline: stem separation (Demucs), transcription (faster-whisper), BPM/key/loudness (librosa, pyloudnorm), version similarity scoring
- **Runs release cycles** — T-4 weeks through release day, with milestones as real calendar events and SMS nudges
- **Convenes a listening panel** — sends candidate tracks to trusted humans over iMessage, collects and catalogs verdicts
- **Works on a schedule** — 13 cron jobs drive daily agent routines ([AGENT_SCHEDULE.md](AGENT_SCHEDULE.md))
- **Talks to the world through MCP** — Google Calendar, iMessage, browser automation, and research tools are wired into agent profiles as tools; the system itself is also exposed as an MCP server (`mcp_server.py`) and an HTTP API (`http_api.py`)
- **Has a face** — a Tauri desktop app for catalog, sessions, and release dashboards

## Architecture

```
DAW export → file_watcher → hermes.db (SQLite) ← coordination engine ← agent profiles
                 │                                      │
           audio_analysis                        MCP integrations
        (stems, transcription,              (Calendar, iMessage, browser,
         BPM/key, similarity)                    research, Twilio SMS)
                 │                                      │
          session_intelligence ──────────── desktop-app (Tauri) / http_api
```

- **Python 3.12**, dependency management via [uv](https://docs.astral.sh/uv/)
- **SQLite** as the single source of truth (Litestream replication supported)
- **Backblaze B2** for audio storage (audio never lives in git)
- Deployable to **Render** (`render.yaml`) or run entirely local

## Quickstart

```bash
git clone <this-repo> && cd ai-record-label
uv sync
cp .env.example .env   # fill in your keys — each one documents where to get it
./scripts/launch.sh    # starts services + desktop app (--no-app for headless)
```

Requires: Python 3.12+, an Anthropic API key, and whichever integrations you enable in `.env` (Twilio for SMS, B2 for storage, Google OAuth for calendar — all optional, the analysis pipeline runs without them).

Run the tests:

```bash
./scripts/run_tests.sh
```

## Docs

- [FEATURES.md](FEATURES.md) — the full feature map and the label-history research behind it
- [AGENT_SCHEDULE.md](AGENT_SCHEDULE.md) — what each agent does and when
- [OPERATIONS.md](OPERATIONS.md) — running it day to day
- [STEM_SEPARATION.md](STEM_SEPARATION.md) — the audio pipeline in depth

## Why

I'm a producer ([Young Denzel](https://just-inn-case.bandcamp.com/)) and an applied AI engineer. Independent artists do the work of an entire label staff alone, badly, at 1am. I studied how the great labels actually operated and hired agents to do those jobs instead.

## License

[MIT](LICENSE)
