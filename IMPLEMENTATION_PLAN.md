---
title: AI Record Label — Complete Implementation Plan
status: DRAFT — Pending Review
date: 2026-05-15
runtime: Hermes Agent
---

# AI Record Label — Implementation Plan

## Overview

A productivity simulation where solo musicians "sign" to their own AI-powered record label. AI agents (A&R, Manager, Creative Director, Bandcamp Agent) simulate label staff via SMS, desktop app, and voice — providing accountability, creative feedback, and release execution. Everything runs on Hermes Agent as the core runtime.

**Key constraints:**
- Hermes Agent is the runtime for ALL agents, orchestration, memory, and messaging
- Gemini 3.1 Pro for audio analysis (multimodal audio understanding)
- Twilio for SMS delivery
- Bandcamp agent is pre-built (will be provided for integration later)
- OpenRouter for LLM routing across agents

---

## Resolved Architecture Decisions

### 1. Bandcamp Agent — Pre-Built, Integrate as MCP Tool
The Bandcamp agent is a fully working FastAPI application (`bandcampagent/backend/main.py`, 2800+ lines, 50+ endpoints) with:

**Existing capabilities we use directly:**
- `POST /scan` — scan a directory for albums, extract metadata, check upload status
- `POST /library/preflight-check` — validate albums are ready (cover art, metadata, track numbers)
- `POST /library/review/decision` — approval gate (accepts `album_paths`, `approved`, `reviewer`, `note`)
- `POST /jobs/upload` — async job queue for Bandcamp uploads (with `publish` and `dry_run` flags)
- `GET /jobs/{job_id}` — poll job status (queued → running → completed/completed_with_failures)
- `POST /generate-art` — AI cover art generation via Gemini (already integrated)
- `POST /metadata` — write/update audio file metadata (ID3 tags)
- `POST /convert` — convert lossy audio to WAV for Bandcamp
- `POST /pipeline/tasks` — full pipeline: ingest → validate → AI metadata → cover gen → upload
- `GET /system/readiness` — health check (cookies, artist URLs, upload capability)
- `GET /monitoring/summary` — upload stats, job history
- `POST /library/review/decision` — publish review/approval gate (mirrors our state machine's approval concept)

**Pipeline Runtime stages** (already defined in `pipeline_runtime.py`):
```
AUDIOMACK_INGEST → AUDIO_VALIDATION → AUDIO_INTELLIGENCE → COVER_GENERATION → BANDCAMP_UPLOAD
```

**Integration approach:** The Bandcamp agent runs as a sidecar process alongside Hermes. The Hermes Bandcamp Agent profile calls the FastAPI endpoints via HTTP as MCP tools. When the coordination engine reaches `RELEASE_READY`, it:
1. Calls `POST /library/preflight-check` to validate the album directory
2. Calls `POST /library/review/decision` with `approved: true, reviewer: "a_and_r"` 
3. Calls `POST /jobs/upload` to queue the upload job
4. Polls `GET /jobs/{job_id}` until completion
5. On success, transitions state to `RELEASED`

**The Bandcamp agent's own approval gate** (`_is_publish_approved`) aligns perfectly with our state machine — we just need to call the review endpoint when A&R + Creative Director + Manager have all cleared their gates.

**What the Bandcamp agent already handles that we don't need to build:**
- Audio format conversion (lossy → WAV)
- Metadata validation and enrichment
- Cover art generation (Gemini)
- Cookie-based Bandcamp auth (no fragile browser scraping)
- Upload job queue with retry logic
- Audit logging for all operations
- Remote sync (compare local library vs what's on Bandcamp)

### 2. Audio Analysis — Gemini 3.1 Pro Multimodal Pipeline
Gemini 3.1 Pro has native audio multimodal input. The pipeline:
1. **Gemini 3.1 Pro** ingests the raw audio file and returns structured creative analysis (arrangement, energy, instruments, timestamps, mood, genre, mix observations)
2. **Hermes A&R agent** (powered by Claude via OpenRouter) takes Gemini's analysis + the artist's accumulated taste/listening memory → generates in-character feedback
3. **Audio Memory:** Gemini's analysis is stored in a dedicated `audio_memory` schema so the system builds a growing understanding of the artist's sound, patterns, strengths, and weaknesses over time. Each analysis cross-references past tracks.

### 3. Release Coordination — State Machine via Hermes Kanban
The release pipeline is modeled as a Hermes Kanban board with explicit states, agent gates, and timeout rules. Inter-agent coordination happens through Hermes's native multi-agent Kanban system — agents watch for state transitions on the board and react according to their rules.

```
Release Pipeline States:
  DRAFT          → Track uploaded, awaiting A&R listen
  IN_REVIEW      → A&R is reviewing (audio analysis in progress)
  FEEDBACK_GIVEN → A&R gave notes, awaiting artist revision or acceptance
  APPROVED       → A&R explicitly approved (artist said "ship it" or similar)
  ART_NEEDED     → Approved track, Creative Director notified for artwork
  ART_SUBMITTED  → Artist submitted artwork for review
  ART_APPROVED   → Creative Director approved artwork
  RELEASE_READY  → Manager has set release date, all gates clear
  PREFLIGHT      → Bandcamp Agent validating album (preflight check)
  UPLOADING      → Bandcamp Agent upload job queued/running
  RELEASED       → Release confirmed live on Bandcamp

Transitions:
  DRAFT → IN_REVIEW:           Automatic on file detection
  IN_REVIEW → FEEDBACK_GIVEN:  Automatic after audio analysis completes
  FEEDBACK_GIVEN → DRAFT:      Artist uploads revision
  FEEDBACK_GIVEN → APPROVED:   Artist or A&R explicitly approves
  APPROVED → ART_NEEDED:       Automatic, notifies Creative Director
  ART_NEEDED → ART_SUBMITTED:  Artist uploads artwork
  ART_SUBMITTED → ART_NEEDED:  Creative Director rejects (sends notes)
  ART_SUBMITTED → ART_APPROVED: Creative Director approves
  ART_APPROVED → RELEASE_READY: Manager sets date (can be immediate)
  RELEASE_READY → PREFLIGHT:   Bandcamp Agent runs POST /library/preflight-check
  PREFLIGHT → UPLOADING:       Preflight passed, POST /jobs/upload queued
  PREFLIGHT → ART_NEEDED:      Preflight failed (missing cover) → Creative Director notified
  PREFLIGHT → FEEDBACK_GIVEN:  Preflight failed (metadata issues) → A&R notified
  UPLOADING → RELEASED:        Job completed successfully (poll GET /jobs/{job_id})
  UPLOADING → RELEASE_READY:   Job failed → Manager notified, retry available

Timeout rules:
  FEEDBACK_GIVEN >7 days → Manager nags via SMS
  ART_NEEDED >3 days before release date → Creative Director escalates
  RELEASE_READY missed date → Manager texts "we missed the window, new date?"

Rollback:
  Any state can go back to DRAFT if artist requests a redo
  APPROVED can be revoked by A&R if artist uploads a worse revision
```

**Approval is explicit.** In SMS: musician says "approved" / "ship it" / "let's go" (parsed via Claude intent detection with 0.8 confidence threshold — below that, agent asks for clarification in-character). In desktop app: a button.

---

## Database Schema (SQLite via Hermes, WAL mode)

```sql
-- Artist profile and preferences
CREATE TABLE artist_profile (
    id INTEGER PRIMARY KEY DEFAULT 1,
    name TEXT NOT NULL,
    genre TEXT,
    subgenres TEXT,        -- JSON array
    influences TEXT,       -- JSON array
    sound_description TEXT,
    bandcamp_url TEXT,
    quiet_hours_start TEXT,  -- "22:00"
    quiet_hours_end TEXT,    -- "09:00"
    quiet_days TEXT,         -- JSON array ["saturday", "sunday"]
    timezone TEXT DEFAULT 'America/Los_Angeles',
    onboarded_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tracks with versioning
CREATE TABLE tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,  -- SHA-256 for dedup
    file_size INTEGER,
    duration_seconds REAL,
    format TEXT,               -- wav, mp3, flac, aiff
    parent_track_id INTEGER REFERENCES tracks(id),  -- links revisions to original
    version INTEGER DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'DRAFT',
    project_id INTEGER REFERENCES projects(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Projects (singles, EPs, albums — the "Deal")
CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    type TEXT NOT NULL,         -- 'single', 'ep', 'album'
    state TEXT NOT NULL DEFAULT 'active',
    target_track_count INTEGER,
    target_release_date TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Audio analysis results from Gemini 3.1 Pro
CREATE TABLE audio_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER NOT NULL REFERENCES tracks(id),
    model_used TEXT NOT NULL DEFAULT 'gemini-3.1-pro',
    bpm REAL,
    musical_key TEXT,
    energy_curve TEXT,         -- JSON array of {timestamp, energy_level}
    structure TEXT,            -- JSON: {intro: "0:00-0:15", verse1: "0:15-0:45", ...}
    instruments TEXT,          -- JSON array
    genre_tags TEXT,           -- JSON array
    mood_tags TEXT,            -- JSON array
    mix_observations TEXT,     -- JSON array of {timestamp, observation}
    notable_moments TEXT,      -- JSON array of {timestamp, description, quality_judgment}
    raw_response TEXT,         -- full Gemini response for debugging
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Audio memory: builds over time, cross-references tracks
-- This is the "ears" memory — what Gemini learns about this artist's sound
CREATE TABLE audio_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    -- categories: 'signature_sound', 'recurring_strength', 'recurring_weakness',
    -- 'genre_tendency', 'production_pattern', 'arrangement_habit',
    -- 'energy_preference', 'instrument_palette', 'evolution_note'
    observation TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,     -- 0.0-1.0, grows with more data points
    first_noticed_track_id INTEGER REFERENCES tracks(id),
    supporting_track_ids TEXT,        -- JSON array of track IDs that confirm this
    times_observed INTEGER DEFAULT 1,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Agent feedback log (all agent ↔ artist messages)
CREATE TABLE feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER REFERENCES tracks(id),
    project_id INTEGER REFERENCES projects(id),
    agent TEXT NOT NULL,       -- 'a_and_r', 'manager', 'creative_director', 'bandcamp'
    message TEXT NOT NULL,
    channel TEXT NOT NULL,     -- 'sms', 'desktop', 'voice'
    direction TEXT NOT NULL,   -- 'outbound' or 'inbound'
    intent TEXT,               -- parsed: 'approval', 'rejection', 'revision', 'feedback', 'question', 'nag', 'delay', 'casual'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Release state machine audit log
CREATE TABLE release_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER NOT NULL REFERENCES tracks(id),
    from_state TEXT,
    to_state TEXT NOT NULL,
    changed_by TEXT NOT NULL,  -- agent name or 'artist'
    reason TEXT,
    bandcamp_job_id TEXT,      -- links to Bandcamp agent job queue
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Artwork submissions
CREATE TABLE artwork (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER REFERENCES tracks(id),
    project_id INTEGER REFERENCES projects(id),
    file_path TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'submitted',  -- submitted, approved, rejected
    review_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Bandcamp integration tracking
-- The Bandcamp agent manages its own release data internally;
-- this table tracks the handoff between our system and theirs
CREATE TABLE bandcamp_releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER NOT NULL REFERENCES tracks(id),
    album_path TEXT NOT NULL,           -- path to album dir on disk (Bandcamp agent input)
    bandcamp_job_id TEXT,               -- from POST /jobs/upload response
    bandcamp_album_id TEXT,             -- from .bandcamp_info.json after upload
    preflight_result TEXT,              -- JSON: result of POST /library/preflight-check
    upload_status TEXT DEFAULT 'pending', -- pending, preflight_passed, uploading, uploaded, failed
    publish_approved_at TIMESTAMP,      -- when POST /library/review/decision was called
    uploaded_at TIMESTAMP,
    bandcamp_url TEXT,                  -- final Bandcamp URL after publish
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Scheduled messages (timing engine)
CREATE TABLE scheduled_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    channel TEXT NOT NULL,
    message TEXT NOT NULL,
    scheduled_for TIMESTAMP NOT NULL,
    sent_at TIMESTAMP,
    context TEXT               -- JSON: why this message, what triggered it
);

-- Deal board gamification
CREATE TABLE milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,        -- 'Demo Review', 'Mix Approval', 'Master Delivery', 'Artwork', 'Release'
    gate_agent TEXT NOT NULL,  -- which agent must approve this milestone
    state TEXT NOT NULL DEFAULT 'pending',  -- pending, active, cleared, skipped
    cleared_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Creation streaks and stats
CREATE TABLE artist_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_type TEXT NOT NULL,   -- 'streak', 'reputation', 'weekly_summary'
    value TEXT NOT NULL,       -- JSON payload
    period_start TEXT,         -- for time-bound stats
    period_end TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## Parallel Workstreams

6 workstreams build in parallel with zero cross-dependencies. The Bandcamp agent (pre-built) integrates later. All workstreams produce Hermes-native artifacts (SOUL.md profiles, MCP tools, Kanban configs, SQLite schemas).

---

### Workstream 1: Agent Personality Profiles (SOUL.md files)
**Type:** Writing / prompt engineering — no code
**Output:** 4 Hermes SOUL.md profiles + tool access configs

```
agents/
  a_and_r/
    SOUL.md             — personality, tone, feedback style, learning instructions
    tools.yaml          — access to: audio_analysis, audio_memory, tracks, feedback
  manager/
    SOUL.md
    tools.yaml          — access to: projects, milestones, scheduled_messages, artist_stats
  creative_director/
    SOUL.md
    tools.yaml          — access to: artwork, image_analysis (Gemini Vision), tracks
  bandcamp/
    SOUL.md
    tools.yaml          — access to: bandcamp_releases, tracks, artwork + HTTP calls to Bandcamp FastAPI
```

Each SOUL.md includes:
- **Identity:** Name, role, personality, speaking style
- **Voice examples:** 10+ example messages per channel (SMS, desktop)
- **Boundaries:** What this agent NEVER does (A&R never gives mix-engineering advice, Manager never gives creative feedback)
- **Interaction patterns:** Responses to artist pushback, silence, enthusiasm, frustration
- **Escalation rules:** When to hand off to another agent
- **DND behavior:** In-character handling of quiet hours ("Alright, I'll back off. But Monday, we talk.")
- **Learning instructions:** What to observe and store in audio_memory/taste tables after each interaction
- **Hermes skill format:** Written so Hermes can auto-refine the skill over time based on interactions

**A&R-specific:** Feedback must reference specific timestamps and musical elements from the Gemini analysis. Never generic. One genuinely insightful observation per track minimum.

**Manager-specific:** Knows the release state machine. Proactively moves things forward. Calculates streaks and generates weekly summaries.

**Creative Director-specific:** Reviews artwork using Gemini Vision. Maintains brand consistency by referencing past artwork decisions stored in memory.

**Acceptance criteria:**
- Each agent holds a 10-message SMS conversation that feels like a real industry person
- A&R references specific timestamps from audio analysis
- Manager correctly cites deadlines and streak data
- Creative Director references brand consistency with past releases
- All agents respect DND while staying in character

---

### Workstream 2: Audio Analysis Pipeline (Gemini 3.1 Pro)
**Type:** Python service, registered as Hermes MCP tool
**Output:** MCP tool that takes an audio file → returns structured analysis → updates audio_memory

```
audio_analysis/
  __init__.py
  analyzer.py            — main entry: analyze(file_path) → AudioAnalysis
  gemini_client.py       — Gemini 3.1 Pro audio upload + structured prompting
  memory_builder.py      — cross-references new analysis against audio_memory, updates patterns
  models.py              — pydantic models for AudioAnalysis, AudioMemoryEntry, etc.
  prompts/
    analysis_prompt.md   — the Gemini prompt for audio analysis
    memory_prompt.md     — prompt for pattern detection across tracks
  hermes_tool.yaml       — MCP tool definition for Hermes registration
  tests/
    test_analyzer.py
    test_memory_builder.py
    fixtures/            — 3-4 short test audio files (10-30 sec)
```

**Gemini 3.1 Pro Analysis Pipeline:**
1. Upload audio file to Gemini 3.1 Pro via multimodal API
2. Prompt requests structured JSON: arrangement structure with timestamps, energy curve, instrument identification, genre/mood classification, mix observations with timestamps, notable moments (good and bad), overall vibe assessment
3. Store raw analysis in `audio_analyses` table
4. **Memory building step:** Feed the new analysis + all past analyses to a memory-building prompt that detects patterns:
   - "This is the third track with a weak bridge section" → `audio_memory` entry: category='recurring_weakness', observation='bridge sections tend to lose energy', confidence increases
   - "Artist is gravitating toward darker, more minimal production" → category='evolution_note'
   - "The 808 patterns are becoming a signature" → category='signature_sound'
5. Update `audio_memory` table: increment `times_observed` for confirmed patterns, add new observations, adjust confidence scores

**Memory query interface** (for A&R agent to use):
- `get_artist_patterns()` → all audio_memory entries sorted by confidence
- `get_track_context(track_id)` → how this track relates to the artist's catalog
- `get_evolution_arc()` → how the artist's sound has changed over time
- `get_strengths_and_weaknesses()` → recurring patterns by category

**Supported formats:** WAV, FLAC, MP3, AIFF, OGG
**Max file size:** 50MB

**Acceptance criteria:**
- Given a WAV file, returns valid AudioAnalysis with timestamps
- audio_memory table grows with each analysis (pattern detection works)
- Second analysis of similar-sounding track references first track's patterns
- After 5+ tracks, `get_artist_patterns()` returns meaningful observations
- Handles corrupt/empty files gracefully

---

### Workstream 3: Desktop Companion App
**Type:** Tauri 2.0 (Rust + React/TypeScript)
**Output:** Cross-platform desktop app that reads Hermes state and provides the primary UI

```
desktop-app/
  src-tauri/
    src/
      main.rs
      commands/
        db.rs              — read from Hermes SQLite (read-only)
        files.rs           — file watcher bridge, audio drop handling
        agents.rs          — send messages to agents via Hermes
  src/
    App.tsx
    pages/
      Hub.tsx              — main dashboard: agent threads, statuses, deal overview
      Dropbox.tsx          — drag-and-drop audio upload zone
      DealBoard.tsx        — project management as a record deal
      ReleaseWall.tsx      — portfolio archive of completed releases
      Onboarding.tsx       — first-run setup wizard
      Settings.tsx         — DND, quiet hours, preferences
    components/
      AgentThread.tsx      — chat-like thread per agent (SMS-style bubbles)
      TrackCard.tsx        — track with state badge, inline audio player
      StatePipeline.tsx    — visual release state machine (DRAFT → ... → RELEASED)
      ReleasePackage.tsx   — formatted release details for Bandcamp upload
      AudioDropzone.tsx    — drag-drop with format validation + progress
      MessageBubble.tsx    — styled message display (different per agent)
      DealCard.tsx         — project/deal visual with milestone progress
      StreakBadge.tsx       — creation streak display
      ReputationScore.tsx  — label reputation gamification display
    hooks/
      useHermesDB.ts       — reads Hermes SQLite via Tauri IPC commands
      useFileWatcher.ts    — subscribes to file watcher events
      useAgentMessages.ts  — real-time agent message feed
      useReleaseState.ts   — track state machine state
    lib/
      state-machine.ts     — release state definitions (mirrors backend)
      audio-formats.ts     — supported format validation
      hermes-bridge.ts     — Hermes API client (messages, commands)
```

**Desktop ↔ Hermes interface:**
- **Reads:** Direct SQLite access (read-only) via Tauri Rust commands. Hermes SQLite in WAL mode supports concurrent readers.
- **Writes:** Posts to Hermes via its HTTP/WebSocket API for sending agent messages and triggering actions. If Hermes doesn't expose an HTTP API, writes go to a `desktop_commands` table that Hermes polls.

**Onboarding flow (first run):**
1. "Sign to your label" — artist name, genre, influences
2. Point to DAW export folder
3. Phone number for SMS (Twilio verification)
4. Set quiet hours / DND preferences
5. "Meet your team" — each agent introduces themselves via SMS
6. Drop first track → A&R feedback within 2 minutes

**Acceptance criteria:**
- Drag-and-drop audio triggers file watcher
- All 4 agent threads display with real-time messages
- Deal Board shows visual pipeline per track with correct states
- Release Wall shows completed releases with cover art
- Onboarding populates `artist_profile` and configures Hermes
- DND toggle immediately pauses all agent messages
- Works on macOS and Windows

---

### Workstream 4: File Watcher Service
**Type:** Python service, registered as Hermes MCP tool
**Output:** Watches a folder for new audio files, creates track records, triggers A&R

```
file_watcher/
  __init__.py
  watcher.py            — main loop using watchdog library
  validator.py          — audio format validation (magic bytes, size, corruption check)
  track_registry.py     — creates track records in SQLite, links revisions
  naming_parser.py      — best-effort metadata from filenames
  hermes_tool.yaml      — MCP tool definition for Hermes registration
  tests/
    test_watcher.py
    test_validator.py
    test_naming_parser.py
```

**On new file detected:**
1. Wait 2 seconds (DAWs write incrementally)
2. Validate: check magic bytes for audio format, reject non-audio
3. SHA-256 hash → check `tracks.file_hash` for exact dedup
4. Parse filename: "Track 3 - rough mix v2.wav" → title="Track 3", version hint=2
5. If filename fuzzy-matches existing track title → set `parent_track_id`, increment version
6. Create `tracks` record with state=DRAFT
7. Emit Hermes event: `new_track_detected` with track_id → triggers coordination engine

**Ignores:** Files <10KB, files >200MB, non-audio formats
**Supported:** WAV, FLAC, MP3, AIFF, OGG

**Acceptance criteria:**
- WAV dropped in watch folder → track record created within 5 seconds
- Duplicate files (same hash) silently skipped
- Corrupt files logged, agent NOT notified
- "v2" / "revision" in filename links to parent track
- Cross-platform: macOS, Windows, Linux

---

### Workstream 5: Hermes Runtime Configuration
**Type:** Infrastructure / DevOps — Hermes setup, Twilio, OpenRouter, Kanban
**Output:** Fully configured Hermes instance ready to receive agent profiles and MCP tools

```
hermes-config/
  docker-compose.yml       — Hermes + SQLite volume + env vars
  .env.example             — all required env vars documented
  profiles/                — directory for SOUL.md files (populated from WS1)
  tools/
    audio_analysis.yaml    — MCP tool definition (wraps WS2)
    file_watcher.yaml      — MCP tool definition (wraps WS4)
    bandcamp_agent.yaml    — MCP tool wrapping pre-built Bandcamp FastAPI (HTTP calls)
    image_analysis.yaml    — Gemini Vision for Creative Director
    calendar.yaml          — scheduling tool for Manager
  kanban/
    release_pipeline.yaml  — Kanban board: states from state machine above
  messaging/
    twilio_config.yaml     — Twilio account SID, auth token, phone number
    timing_engine.yaml     — DND-aware scheduling rules per agent
  scripts/
    setup.sh               — full setup: install Hermes, configure, migrate DB
    migrate_db.sh          — applies SQLite schema
    test_sms.sh            — send test SMS to verify Twilio works
    verify_agents.sh       — smoke test all 4 agents
```

**Hermes configuration tasks:**
1. Install Hermes Agent (Docker recommended)
2. Configure OpenRouter:
   - A&R creative feedback → Claude Opus via OpenRouter
   - Manager scheduling/nagging → Claude Sonnet via OpenRouter
   - Creative Director → Claude Opus via OpenRouter + Gemini Vision direct
   - Audio analysis → Gemini 3.1 Pro direct (not through OpenRouter)
3. Configure Twilio SMS:
   - Single phone number (agent identifies by name in message)
   - Inbound webhook → Hermes message handler
   - Outbound via Twilio REST API
4. Register 4 agent profiles (SOUL.md from WS1)
5. Set up Kanban board matching the release state machine
6. Register MCP tools: audio_analysis (WS2), file_watcher (WS4), image_analysis, calendar
7. Configure Bandcamp agent MCP tool — HTTP bridge to the Bandcamp FastAPI backend:
   - The Bandcamp agent (`bandcampagent/backend/main.py`) runs as a sidecar via `uvicorn main:app --port 8000`
   - MCP tool definition wraps key endpoints as callable tools for the Hermes Bandcamp Agent profile:
     - `bandcamp_scan(path)` → `POST /scan`
     - `bandcamp_preflight(album_paths)` → `POST /library/preflight-check`
     - `bandcamp_approve(album_paths, reviewer)` → `POST /library/review/decision`
     - `bandcamp_upload(album_paths, publish, dry_run)` → `POST /jobs/upload`
     - `bandcamp_job_status(job_id)` → `GET /jobs/{job_id}`
     - `bandcamp_generate_art(path, api_key)` → `POST /generate-art`
     - `bandcamp_readiness()` → `GET /system/readiness`
   - Environment: `BACKEND_API_TOKEN` for auth, `BCA_ARTIST_URL` for default artist
   - Start script: add Bandcamp backend to docker-compose or startup script alongside Hermes
8. Apply SQLite schema via migration script
9. Configure message timing engine:
   - A&R: evenings (7-10pm), occasional late night
   - Manager: mornings (9-11am)
   - Creative Director: afternoons (2-5pm)
   - Random jitter: 0-45 minutes on all scheduled messages
   - Max 8 agent messages per day
   - DND-aware: queue messages during quiet hours, deliver when DND lifts with in-character acknowledgment

**Acceptance criteria:**
- All 4 agents respond to test SMS in-character
- DND/quiet hours respected — no messages during configured times
- Audio analysis MCP tool callable by A&R agent
- Kanban board reflects release state machine states
- Twilio send/receive working for inbound and outbound SMS

---

### Workstream 6: Coordination Engine + Gamification
**Type:** Python — Hermes event handlers, state machine, game mechanics
**Output:** The logic that connects agents through the release pipeline + streaks/reputation

```
coordination/
  __init__.py
  engine.py              — main Hermes event processor
  state_machine.py       — release states, transitions, guards, timeouts
  rules.py               — coordination rules (event → agent actions)
  intent_parser.py       — classify artist SMS intent via Claude
  nag_scheduler.py       — timeout-based nag rules
  bandcamp_bridge.py     — HTTP client for Bandcamp FastAPI endpoints
  gamification/
    deal_board.py        — deal/project lifecycle, milestone tracking
    streaks.py           — creation cadence tracking
    reputation.py        — label reputation scoring
    weekly_summary.py    — end-of-week/month data for Manager
  hermes_handlers.yaml   — Hermes event handler registrations
  tests/
    test_state_machine.py
    test_rules.py
    test_intent_parser.py
    test_bandcamp_bridge.py
    test_streaks.py
    test_reputation.py
```

**`bandcamp_bridge.py`** — HTTP client that wraps the Bandcamp FastAPI:
```python
# Interface for the coordination engine to call the Bandcamp agent
class BandcampBridge:
    def __init__(self, base_url="http://localhost:8000", api_token=None):
        ...

    def check_readiness(self) -> dict:
        """GET /system/readiness — verify cookies, artist URL, upload capability"""

    def preflight(self, album_paths: list[str]) -> dict:
        """POST /library/preflight-check — validate albums ready for upload"""

    def approve_for_publish(self, album_paths: list[str], reviewer: str, note: str = "") -> dict:
        """POST /library/review/decision — set publish approval"""

    def queue_upload(self, album_paths: list[str], publish=False, dry_run=False) -> dict:
        """POST /jobs/upload — queue async upload job, returns job_id"""

    def get_job_status(self, job_id: str) -> dict:
        """GET /jobs/{job_id} — poll job status"""

    def generate_art(self, album_path: str, api_key: str, vibe="default", prompt=None) -> dict:
        """POST /generate-art — generate cover art via Gemini"""

    def scan_library(self, path: str) -> dict:
        """POST /scan — scan directory for albums"""

    def update_metadata(self, file_path: str, **tags) -> dict:
        """POST /metadata — update audio file metadata"""
```

**Coordination Rules (Hermes event → agent action):**

| Event | Action | State Transition |
|-------|--------|-----------------|
| `new_track_detected` | A&R sends "listening now" SMS, triggers audio analysis | DRAFT → IN_REVIEW |
| `audio_analysis_complete` | A&R generates + sends feedback | IN_REVIEW → FEEDBACK_GIVEN |
| `artist_approves` (intent parsed) | Notify Manager + Creative Director | FEEDBACK_GIVEN → APPROVED → ART_NEEDED |
| `artwork_submitted` | Creative Director reviews via Gemini Vision | — |
| `art_approved` | Notify Manager to set date | ART_SUBMITTED → ART_APPROVED |
| `art_rejected` | Creative Director sends notes | ART_SUBMITTED → ART_NEEDED |
| `release_date_set` | Bandcamp Agent runs preflight | ART_APPROVED → RELEASE_READY → PREFLIGHT |
| `preflight_passed` | Bandcamp Agent approves + queues upload | PREFLIGHT → UPLOADING |
| `preflight_failed` | Route back to A&R or Creative Director | PREFLIGHT → FEEDBACK_GIVEN or ART_NEEDED |
| `upload_completed` | Celebrate, update stats, store Bandcamp URL | UPLOADING → RELEASED |
| `upload_failed` | Manager notified, retry available | UPLOADING → RELEASE_READY |
| `timeout_feedback_7d` | Manager nags about sitting on notes | — |
| `timeout_art_3d` | Creative Director escalates about missing visuals | — |
| `timeout_release_missed` | Manager suggests new date | — |

**Intent parsing** (artist SMS → structured intent):
- Uses Claude (via Hermes) to classify: `approve`, `reject`, `revise`, `delay`, `question`, `casual`
- Confidence threshold 0.8 — below that, agent asks for clarification in-character
- Examples: "ship it" → approve, "needs work" → revise, "give me till friday" → delay

**Gamification:**

Deal Board:
- New project → Deal created with milestones: Demo Review → Mix Approval → Master Delivery → Artwork → Release
- Each milestone has an agent gate (A&R must approve before Mix Approval clears)
- Visual progress in desktop app styled as a record contract

Streaks:
- Upload at least 1 track per week = streak maintained
- Manager celebrates: "3 weeks in a row. You're locked in."
- Manager flags gaps: "It's been 12 days. Everything okay?"

Label Reputation:
- Tracks completed: +10
- Released on schedule: +20
- Missed deadline: -5
- Streak weeks: +5
- "Your label has a 78% completion rate"

Weekly Summary (Manager generates):
- Tracks in progress / completed / released
- Streak status
- Upcoming deadlines
- Reputation change

**Acceptance criteria:**
- State transitions are atomic and logged in release_states table
- "ship it" SMS correctly triggers full approval flow
- Timeout nags fire at configured intervals
- Creative Director rejection returns track to ART_NEEDED
- Full pipeline: drop file → feedback → approve → art → date → package
- Streak counter tracks weekly cadence accurately
- Reputation score updates on all defined events

---

## Integration Phase

### Phase 7: Wire Everything Together
**After all 6 workstreams complete — sequential, not parallelizable**

1. Load SOUL.md profiles (WS1) into Hermes runtime (WS5)
2. Register audio analysis (WS2) and file watcher (WS4) as MCP tools in Hermes
3. Connect coordination engine (WS6) event handlers to Hermes event system
4. Wire desktop app (WS3) to Hermes SQLite + messaging API
5. End-to-end test: drop audio file → A&R feedback via SMS → approve via SMS → Creative Director requests art → art approved → Manager sets date → release package prepared
6. Smoke test every desktop app screen with live data
7. Verify DND pauses all channels immediately
8. Test edge cases: duplicate approval, rejection after approval, revision after approval, timeout nags

### Phase 8: Bandcamp Agent Integration
**The Bandcamp agent code is at a separate local repo (not included)**

1. **Copy/symlink Bandcamp agent into project:**
   ```
   ai-record-label/
   └── bandcamp-agent/     ← copy of bandcampagent/backend/ + external_uploader/
   ```

2. **Add to docker-compose / startup:**
   - Bandcamp FastAPI runs on port 8000 alongside Hermes
   - Set env vars: `BACKEND_API_TOKEN`, `BCA_ARTIST_URL`, `OPENROUTER_API_KEY`
   - Ensure cookies.txt is present for Bandcamp auth

3. **Register MCP tools in Hermes** (from WS5's bandcamp_agent.yaml):
   - Map each endpoint to a callable tool
   - Hermes Bandcamp Agent profile gets access to these tools

4. **Wire coordination engine (WS6) → Bandcamp agent:**
   ```
   RELEASE_READY event:
     1. Resolve album_path from track record (where the final files live)
     2. Call bandcamp_preflight(album_paths=[album_path])
     3. If preflight passes:
        a. Call bandcamp_approve(album_paths=[album_path], reviewer="system")
        b. Call bandcamp_upload(album_paths=[album_path], publish=False, dry_run=False)
           → First upload as DRAFT (safe default)
        c. Store job_id in bandcamp_releases table
        d. State → UPLOADING
     4. If preflight fails:
        a. Parse errors: missing_cover_art → route to Creative Director
        b. Parse errors: missing metadata → route to A&R for metadata fix
        c. Manager gets notified of the issue

   UPLOADING poll loop (via Hermes scheduled task):
     1. Call bandcamp_job_status(job_id)
     2. If completed: State → RELEASED, store bandcamp_url
        → Manager SMS: "It's live. [bandcamp_url]"
        → A&R SMS: "Just heard the final on Bandcamp. Good work."
     3. If failed: State → RELEASE_READY
        → Manager SMS: "Upload hit a snag. I'll retry. Sit tight."
        → Auto-retry once after 5 minutes
   ```

5. **Creative Director ↔ Bandcamp art generation:**
   - When Creative Director needs to generate art, call `bandcamp_generate_art()`
   - This uses the Bandcamp agent's existing Gemini-powered art generator
   - Saves cover.jpg to the album directory
   - Creative Director reviews the result via Gemini Vision

6. **Test the full end-to-end:**
   - Drop audio file → A&R feedback → approve → art generated → art approved → date set → preflight → upload → RELEASED
   - Verify draft appears on Bandcamp
   - Verify the publish approval gate blocks unauthorized publishes
   - Test preflight failure → reroute to correct agent

---

## Onboarding Flow

1. **Install:** Download desktop app (bundles Hermes runtime)
2. **"Sign Your Deal":** Artist name, genre, influences, bio
3. **Connect Phone:** Enter number, Twilio verification
4. **Set Hours:** Quiet hours, DND days
5. **Point to DAW Folder:** Select bounce/export folder for file watcher
6. **Meet Your Team:** Each agent sends intro SMS:
   - A&R: "Hey, I'm your A&R. Drop me something and I'll tell you what I think. No bullshit."
   - Manager: "Welcome to the roster. Let's set your first deadline. What are you working on?"
   - Creative Director: "Before you drop anything — do you have a visual identity? Send me references."
7. **Drop First Track:** File watcher detects it, A&R sends feedback via SMS within 2 minutes.

---

## Open Questions

1. **Single SMS number or per-agent numbers?** Per-agent is more immersive but 4x Twilio cost. Recommendation: single number, agent identifies by name.
2. **Tauri or Electron?** Recommendation: Tauri 2.0 — smaller, faster, stable enough for this.
3. **Voice/TTS in MVP?** Recommendation: defer. SMS + desktop is enough for MVP.
4. **Multi-user?** Recommendation: single-tenant MVP, but add `artist_id` foreign keys now for cheap future-proofing.

---

## Complete File Tree

```
ai-record-label/
├── agents/                            # Workstream 1
│   ├── a_and_r/
│   │   ├── SOUL.md
│   │   └── tools.yaml
│   ├── manager/
│   │   ├── SOUL.md
│   │   └── tools.yaml
│   ├── creative_director/
│   │   ├── SOUL.md
│   │   └── tools.yaml
│   └── bandcamp/
│       ├── SOUL.md
│       └── tools.yaml
├── audio_analysis/                    # Workstream 2
│   ├── __init__.py
│   ├── analyzer.py
│   ├── gemini_client.py
│   ├── memory_builder.py
│   ├── models.py
│   ├── prompts/
│   │   ├── analysis_prompt.md
│   │   └── memory_prompt.md
│   ├── hermes_tool.yaml
│   └── tests/
├── desktop-app/                       # Workstream 3
│   ├── src-tauri/
│   │   └── src/
│   │       ├── main.rs
│   │       └── commands/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── pages/
│   │   ├── components/
│   │   ├── hooks/
│   │   └── lib/
│   ├── package.json
│   └── tauri.conf.json
├── file_watcher/                      # Workstream 4
│   ├── __init__.py
│   ├── watcher.py
│   ├── validator.py
│   ├── track_registry.py
│   ├── naming_parser.py
│   ├── hermes_tool.yaml
│   └── tests/
├── hermes-config/                     # Workstream 5
│   ├── docker-compose.yml
│   ├── .env.example
│   ├── profiles/
│   ├── tools/
│   │   ├── audio_analysis.yaml
│   │   ├── file_watcher.yaml
│   │   ├── bandcamp_agent.yaml        # HTTP bridge to Bandcamp FastAPI
│   │   ├── image_analysis.yaml
│   │   └── calendar.yaml
│   ├── kanban/
│   │   └── release_pipeline.yaml
│   ├── messaging/
│   │   ├── twilio_config.yaml
│   │   └── timing_engine.yaml
│   └── scripts/
│       ├── setup.sh
│       ├── migrate_db.sh
│       ├── test_sms.sh
│       └── verify_agents.sh
├── coordination/                      # Workstream 6
│   ├── __init__.py
│   ├── engine.py
│   ├── state_machine.py
│   ├── rules.py
│   ├── intent_parser.py
│   ├── nag_scheduler.py
│   ├── bandcamp_bridge.py             # HTTP client for Bandcamp FastAPI endpoints
│   ├── gamification/
│   │   ├── deal_board.py
│   │   ├── streaks.py
│   │   ├── reputation.py
│   │   └── weekly_summary.py
│   ├── hermes_handlers.yaml
│   └── tests/
│       ├── test_state_machine.py
│       ├── test_rules.py
│       ├── test_intent_parser.py
│       ├── test_bandcamp_bridge.py
│       ├── test_streaks.py
│       └── test_reputation.py
├── bandcamp-agent/                    # Pre-built — copied from bandcampagent/
│   ├── backend/
│   │   ├── main.py                    # FastAPI app (2800+ lines, 50+ endpoints)
│   │   ├── pipeline_runtime.py        # Pipeline stages + task store
│   │   ├── metadata_service.py        # Audio metadata read/write
│   │   ├── music_library_service.py   # Library scanning
│   │   └── config_service.py          # Runtime config management
│   ├── external_uploader/
│   │   ├── batch_uploader.py          # Actual Bandcamp upload logic
│   │   ├── metadata_utils.py          # Metadata utilities
│   │   └── cookies.txt                # Bandcamp auth (user provides)
│   └── start.sh                       # Startup script (backend on :8000)
├── schema/
│   └── migrations/
│       └── 001_initial.sql
└── IMPLEMENTATION_PLAN.md
```
