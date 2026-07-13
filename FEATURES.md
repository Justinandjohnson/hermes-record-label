# AI Record Label — Feature Build Plan

Everything we're building, organized by priority. Each feature includes what it is,
why it matters, what schema/tools/agent changes are needed, and which MCP integrations
it uses. Derived from deep study of operating principles across Michael Jackson, Prince,
Marvin Gaye, D'Angelo, Quincy Jones, and 19 record labels (Motown through Brainfeeder).

---

## MCP Integration Layer (Foundation — Wire Up First)

Before building features, connect our agents to the real world. These MCP servers
are already available and need to be wired into agent profiles as available tools.

### Google Calendar
- **MCP:** `google-calendar` or `composio GOOGLECALENDAR_*`
- **Used by:** Diane (release dates, deadlines, session scheduling, milestone reminders)
- **Capabilities:** Create events, list events, find free slots, set reminders
- **Use cases:**
  - Diane creates calendar events for every release cycle milestone (T-4, T-3, T-2, T-1, Release Day, T+2)
  - Session scheduling: "block off 2 hours tomorrow for mixing"
  - Deadline reminders synced to real calendar, not just SMS nags
  - Bandcamp Friday reminders (first Friday of each month)

### Gmail
- **MCP:** `composio GMAIL_*`
- **Used by:** Rex (sync pitches, distributor comms), Diane (press outreach drafts), Studio (notification routing)
- **Capabilities:** Send email, create drafts, fetch emails, manage labels, search
- **Use cases:**
  - Rex drafts emails to sync libraries with track metadata attached
  - Diane drafts press outreach emails for release announcements
  - Studio monitors a dedicated inbox for inbound sync inquiries or press responses
  - All emails go through draft → artist approval → send (never auto-send)

### iMessage
- **MCP:** `imessage` (send-message, send-file, get-messages, search-messages, list-contacts, find-contact)
- **Used by:** Studio conductor (artist communication), QC Panel (sending tracks to listening panel)
- **Capabilities:** Send messages and files, read conversations, search history, access contacts
- **Use cases:**
  - Human QC Panel: send high-quality audio files to listening panel contacts
  - Collect and catalog responses from panel members
  - Backup communication channel alongside Twilio SMS

### Browser Automation
- **MCP:** `Claude_in_Chrome`, `playwright`, `chrome-devtools`, `composio BROWSER_TOOL_*`
- **Used by:** All agents (registration, platform management, research, monitoring)
- **Capabilities:** Navigate, click, fill forms, read pages, execute JS, take screenshots
- **Use cases:**
  - Royalty organization registration (ASCAP/BMI, MLC, SoundExchange)
  - Bandcamp page management and analytics
  - Sync platform submissions (That Pitch, Songtradr)
  - Monitoring royalty dashboards for incoming payments
  - DistroKid/Symphonic uploads when ready for DSP distribution
  - Research: market trends, similar artist activity, playlist curator contacts

### Perplexity (Research)
- **MCP:** `perplexity` (search, reason, deep_research)
- **Used by:** All agents for real-time research
- **Use cases:**
  - Nico: research reference artists, genre trends, production techniques
  - Diane: research comparable artist release strategies, venue info
  - Rex: research Bandcamp algorithm changes, platform best practices
  - Mika: research visual trends, album art references, design movements
  - Royalty monitoring: track industry news about PRO policy changes, new collection opportunities

---

## Tier 0 — Work Pattern Intelligence (Core System Upgrade)

This is the always-on backbone. The file watcher already detects new exports from
Ableton — this upgrade makes the system AWARE of the artist's creative rhythm and
uses that awareness across every agent.

### How It Works

**Every export → Calendar event → Pattern detection → Calibrated nudges**

1. When the file watcher detects a new audio export, it creates a Google Calendar event:
   - Title: "Studio Session: [track title or filename]"
   - Time: now (when the export was detected)
   - Description: format, duration, file size, which project (if any)
   - Color: green for new tracks, blue for revisions of existing tracks

2. Over time, this builds a visual creation log on Google Calendar:
   - The artist can see their own work patterns at a glance
   - Diane can read the calendar to analyze when and how often the artist creates
   - Patterns emerge: "most productive Tuesday/Thursday 10pm-1am"

3. Diane monitors the calendar for gaps and adapts:
   - **Day 3 no exports:** Nothing. Normal creative rest.
   - **Day 5:** Soft awareness. Diane notes it internally.
   - **Day 7:** Gentle check-in: "been a week since your last session. everything cool?"
   - **Day 10:** Streak warning: "your streak is about to break. no pressure, but just want you to know"
   - **Day 14+:** Back-off mode: "whenever you're ready. the vault has [N] tracks waiting"
   - These thresholds adjust based on learned patterns (stored in agent_memory)

4. Weekly pattern report (Diane, Sunday evening):
   - Sessions this week vs. last week
   - Most productive day/time window
   - Active projects and their momentum
   - Tracks in each pipeline stage
   - Creation streak status
   - Calendar events for upcoming deadlines

### Session Intelligence

Beyond just detecting files, the system understands sessions:

- **Session clustering:** Multiple exports within a 2-hour window = one session (not 5 separate events)
- **Session duration estimation:** Time between first and last export in a cluster
- **Project association:** If exports land in a project folder, link them to that project automatically
- **Revision detection:** File watcher already tracks parent_track_id — calendar shows "Revision 3 of [track]" not just "new file"

### Post-Session Prompt

After a session ends (no new exports for 30+ minutes after a cluster), Studio sends
a quick SMS: "good session? one sentence — what were you working on and how did it feel?"

- Response stored in `session_notes` table linked to the export events
- Nico can reference these: "you said this session felt 'stuck but found something at the end' — I can hear that, the outro is the strongest section"
- Diane tracks session mood over time: are sessions getting more or less energized?

**Schema changes:**
```sql
CREATE TABLE IF NOT EXISTS session_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT    NOT NULL,
    ended_at        TEXT,
    duration_minutes INTEGER,
    export_count    INTEGER DEFAULT 0,
    project_id      INTEGER REFERENCES projects(id),
    calendar_event_id TEXT,               -- Google Calendar event ID for linking
    session_note    TEXT,                  -- artist's one-sentence reflection
    mood            TEXT,                  -- parsed from note: energized, stuck, exploratory, focused, frustrated
    created_at      TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS creation_streaks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT    NOT NULL,
    ended_at        TEXT,                  -- NULL if streak is active
    length_days     INTEGER,
    longest_gap_hours REAL,               -- longest gap within the streak
    total_exports   INTEGER DEFAULT 0,
    created_at      TEXT    DEFAULT (datetime('now'))
);
```

**New MCP tools:**
- `log_session(started_at, ended_at, export_count, project_id)` — Record a studio session
- `add_session_note(session_id, note)` — Store the artist's post-session reflection
- `get_work_patterns(days)` — Return creation patterns over the last N days (most productive times, session frequency, streak status)
- `get_streak_status()` — Current streak length, at-risk status, longest streak ever
- `create_session_calendar_event(session_id)` — Push the session to Google Calendar

**MCP integrations:**
- `google-calendar` — create session events, read back for pattern analysis
- `imessage` or Twilio — post-session prompt to the artist
- `perplexity` — optional: correlate creation patterns with external factors (full moon? who knows)

**Agent changes:**
- Diane: reads the calendar weekly. Knows the artist's rhythm. Nudges are calibrated to actual patterns, not arbitrary timers. Weekly summary includes session data.
- Nico: references session notes in feedback. "you said you were exploring something at the end of your last session — this track feels like where that was heading"
- Studio: orchestrates the post-session prompt. Doesn't send it every time — learns when the artist responds vs. ignores it (via agent_memory)

### Comprehensive MCP Usage Map (Across All Existing Features)

Every agent should have access to tools that multiply their effectiveness.
Here's the full map of which MCPs enhance which parts of the existing system:

| System Component | MCP Integration | What It Does |
|---|---|---|
| File watcher detects export | Google Calendar | Creates "Studio Session" event with metadata |
| Diane weekly summary | Google Calendar | Reads week's events to compile session count, patterns |
| Diane release deadline | Google Calendar | Creates milestone events with alerts at T-4, T-3, T-2, T-1 |
| Diane nag timing | Google Calendar | Checks if artist has upcoming free time before nagging |
| Nico feedback delivery | iMessage | Send detailed feedback with audio timestamps as iMessage (richer than SMS) |
| Nico reference suggestion | Perplexity | Research the reference track/artist for context before suggesting |
| Nico Yoda Mode | Perplexity + iMessage | Research 3 reference tracks, send links to artist before session |
| Mika reference deck | Perplexity + Browser | Research visual references, capture screenshots of inspiration |
| Mika artwork specs | Browser | Check current Bandcamp art requirements, platform-specific needs |
| Rex Bandcamp upload | Browser | Automate Bandcamp upload if API isn't available |
| Rex Bandcamp stats | Browser | Scrape Bandcamp analytics (plays, sales, followers, geography) |
| Rex release page | Browser | Preview how release page looks before publishing |
| Rex tag research | Perplexity | Research which Bandcamp tags are trending for the artist's genres |
| Studio royalty monitoring | Browser | Log into ASCAP/BMI/MLC/SoundExchange dashboards, check statements |
| Studio royalty news | Perplexity | Monthly scan for PRO policy changes, new collection opportunities |
| Studio panel orchestration | iMessage | Send tracks to panel, monitor responses, collect feedback |
| Diane press outreach | Gmail | Draft and send press release emails (with artist approval) |
| Rex sync pitching | Gmail + Browser | Email sync libraries, submit via That Pitch/Songtradr |
| Diane weekly email digest | Gmail | Send the artist a weekly summary email as a permanent record |
| Diane Bandcamp Friday | Google Calendar | Recurring calendar event on first Friday of each month |
| All agents research | Perplexity | Real-time research on any topic relevant to their domain |
| Studio publishing setup | Browser | Navigate publishing entity setup on PRO websites |
| Diane collaborator scheduling | Google Calendar + iMessage | Coordinate session times with collaborators |

---

## Tier 1 — Build Now

### 1. The Vault (Prince/Rogers Concept)

**What:** Every track lives forever. Nothing is rejected — it's vaulted. Vaulted tracks
can be resurfaced when the artist's ear develops or a new project needs them.

**Why:** Prince's vault held thousands of songs. Rogers cataloged them starting in 1983.
The vault turns output into a long-term catalog asset. A track that doesn't work today
might be exactly what a project needs in six months.

**Schema changes:**
```sql
-- Add VAULT and VAULT_RESURFACED to valid track states
-- (enforced by convention, state column is TEXT)
-- Valid states become: DRAFT, REVIEW, FEEDBACK_GIVEN, APPROVED,
--   ART_SUBMITTED, ART_APPROVED, PREFLIGHT, RELEASED,
--   VAULT, VAULT_RESURFACED

ALTER TABLE tracks ADD COLUMN vault_reason TEXT;
ALTER TABLE tracks ADD COLUMN vault_date TEXT;
```

**New MCP tools:**
- `vault_track(track_id, reason)` — Move a track to VAULT with a reason ("not ready", "doesn't fit current project", "interesting idea needs development")
- `vault_search(mood, key, tempo_range, genre_tags)` — Query audio_analyses to find vault tracks matching a vibe
- `resurface_track(track_id, reason)` — Pull a track back to DRAFT from VAULT

**Agent changes:**
- Nico: when giving feedback on a new track, searches the vault for related material. "this has the same harmonic thing as something you vaulted in March"
- Diane: vault counts show up in weekly summaries. "12 tracks in vault, 3 in active pipeline"
- Studio: when a track is moved to VAULT, it's not a failure — frame it as building the archive

---

### 2. Human Quality Control Panel (Motown Friday Meeting — Real People Edition)

**What:** Instead of LLM-simulated listeners, send the actual track to 5 real people
from the artist's contacts. Collect their honest reactions. Catalog everything.
The modern version of Gordy's Friday 9AM meeting where anyone — even the janitor —
could veto a release.

**Why:** Real human reactions are irreplaceable. A friend who texts back "I played this
three times" is more signal than any AI persona. This builds the artist's community
into the creative process and creates genuine human connection in the pipeline.

**How it works:**
1. Artist adds contacts to their "Listening Panel" in the desktop app (name + phone/iMessage)
2. When a track hits APPROVED and the artist wants outside ears, they trigger `send_to_panel`
3. System sends the track (highest quality file — WAV or FLAC) via iMessage to all panel members
4. System monitors for responses over 48 hours
5. Responses are cataloged with timestamps, sentiment analysis, and key quotes
6. Studio conductor summarizes the panel feedback for the artist
7. Optional: the hot-dog question — system asks each panelist "would you pay $1.29 for this?"

**Schema changes:**
```sql
CREATE TABLE IF NOT EXISTS listening_panel (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    phone       TEXT,                    -- phone number for Twilio SMS
    imessage_id TEXT,                    -- iMessage handle (email or phone)
    relationship TEXT,                   -- "friend", "musician", "producer", "casual listener"
    genre_knowledge TEXT,                -- what genres they know well
    active      INTEGER DEFAULT 1,
    added_at    TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS panel_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id    INTEGER NOT NULL REFERENCES tracks(id),
    status      TEXT    DEFAULT 'sent',  -- sent, collecting, complete, cancelled
    sent_at     TEXT    DEFAULT (datetime('now')),
    closed_at   TEXT,
    summary     TEXT                     -- conductor's synthesis of all responses
);

CREATE TABLE IF NOT EXISTS panel_responses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES panel_sessions(id),
    panelist_id     INTEGER NOT NULL REFERENCES listening_panel(id),
    raw_response    TEXT,                -- their exact words
    sentiment       TEXT,                -- positive, mixed, negative, no_response
    would_buy       INTEGER,             -- 1 = yes, 0 = no, NULL = not asked
    key_quote       TEXT,                -- most telling phrase from their response
    response_time   INTEGER,             -- seconds between send and first response
    listened_count  INTEGER,             -- if they mention replays
    received_at     TEXT    DEFAULT (datetime('now'))
);
```

**New MCP tools:**
- `manage_panel(action, name, phone, imessage_id, relationship)` — Add/remove/list panel members
- `send_to_panel(track_id, message)` — Send track to all active panel members via iMessage with a personal note
- `collect_panel_responses(session_id)` — Check for new iMessage responses, catalog them
- `get_panel_results(session_id)` — Return summary of all responses for a track

**MCP integrations used:** `imessage` (send-file, send-message, get-messages, search-messages, find-contact)

**Agent changes:**
- Nico: after approving a track, suggests "want to send this to your panel before we go further?"
- Studio: orchestrates the panel session — sends, monitors, summarizes
- Diane: tracks panel response patterns over time ("your panel consistently flags your bridges as weak points — pattern confirmed")

---

### 3. Release Cycle Planner (The T-Minus Countdown)

**What:** When Diane sets a release date, auto-generate a backward-planned timeline
with milestones on real calendar dates. Every successful label runs this — Motown
sequenced releases against the Billboard top 5, TDE ran 30-date tours two months
pre-release.

**Timeline template (adapted for indie single-artist):**
- **T-4 weeks:** Masters finalized. Artwork approved by Mika. Distributor submission if using DSPs.
- **T-3 weeks:** Release page copy written. Tags finalized. Pre-save links live (if applicable).
- **T-2 weeks:** Teaser content ready. Bandcamp preflight passed by Rex.
- **T-1 week:** Final review. Rex confirms upload clean and in draft. Files frozen.
- **Release day:** Publish on Bandcamp. Social posts. Email fans.
- **T+2 weeks:** Post-release review. Stats check. Nico reflects. Diane updates streak.

**Schema changes:**
```sql
ALTER TABLE milestones ADD COLUMN due_date TEXT;
ALTER TABLE milestones ADD COLUMN milestone_type TEXT DEFAULT 'custom';
-- milestone_type: 'release_cycle' | 'project' | 'custom'
```

**New MCP tools:**
- `plan_release(track_id, release_date)` — Auto-generates milestones at T-4, T-3, T-2, T-1, T-0, T+2
- `get_release_timeline(track_id)` — Returns all milestones with dates and status
- `check_release_readiness(track_id)` — Quick status: which milestones are cleared vs. overdue

**MCP integrations used:** `google-calendar` (create events for each milestone with reminders)

**Agent changes:**
- Diane: owns the release cycle. Creates the plan, tracks progress, nags on overdue milestones
- Mika: artwork deadline is explicit — knows exactly when art must be approved
- Rex: preflight deadline is explicit — knows when upload must be clean
- Nico: final review deadline is explicit — no open-ended feedback loops once the clock starts

---

### 4. Sync Readiness Tagging (The Catalog Intelligence Layer)

**What:** Auto-tag every track with sync-licensing metadata on top of what Gemini
already produces. Turns the catalog into a pitchable library.

**Why:** Sync licensing is a $2.5B market. One national commercial placement can pay
more than a year of streaming royalties. Platforms like That Pitch distribute to 100+
music libraries with no upfront fees. But you need proper metadata to be discoverable.

**Schema changes:**
```sql
ALTER TABLE audio_analyses ADD COLUMN sync_scene_tags TEXT;
-- JSON array: ["late night drive", "introspective documentary", "coming of age film",
--              "romantic montage", "urban lifestyle", "workout/intensity"]

ALTER TABLE audio_analyses ADD COLUMN vocal_presence TEXT;
-- "instrumental" | "vocal" | "both" | "spoken_word"

ALTER TABLE audio_analyses ADD COLUMN explicit_content INTEGER DEFAULT 0;

ALTER TABLE audio_analyses ADD COLUMN sync_tier TEXT;
-- "A" (release-ready, clean production, broad placement potential)
-- "B" (solid, niche appeal, specific scene fit)
-- "C" (raw/demo quality, needs production work for sync)

ALTER TABLE audio_analyses ADD COLUMN isrc TEXT;
-- International Standard Recording Code — needed for sync paperwork
```

**New MCP tools:**
- `tag_for_sync(track_id)` — Run Gemini analysis focused on sync-relevant attributes, store results
- `get_sync_ready_tracks(scene_type, mood, instrumental_only)` — Query catalog for pitchable tracks
- `generate_sync_sheet(track_id)` — Export a one-page sync pitch sheet (title, BPM, key, mood, scene tags, contact info)

**Agent changes:**
- Nico: sync tags are generated alongside regular audio analysis (extend the Gemini prompt)
- Rex: awareness of sync-ready catalog. Can flag "this track is Tier A for sync — let's make sure metadata is complete"
- Future sync agent: uses `get_sync_ready_tracks` + browser automation to submit to platforms

---

### 5. Autonomous Royalty Registration & Monitoring (The 75% Problem — Solved)

**What:** Agents handle royalty organization registration FOR the artist using browser
automation. After one-time approval, they navigate registration forms, fill in details,
submit, and then monitor dashboards for incoming royalties and industry news. The artist
focuses on music; the system handles the business paperwork.

**Why:** 75% of music royalties go uncollected because artists don't register. Artists
who register within 30 days of first release collect 15-25% more revenue in year one.
Three separate organizations (PRO, MLC, SoundExchange) each require registration.
This is exactly the kind of tedious-but-critical work agents should handle.

**The three organizations:**
- **PRO (ASCAP or BMI)** — performance royalties (radio, streaming, live, TV)
  - Registration: browser automation fills the online form at ascap.com or bmi.com
  - Agent recommends which one based on the artist's genre and situation
  - Ongoing: monitor the PRO dashboard for royalty statements
- **The MLC (mechanicallicensing.com)** — mechanical royalties from streaming
  - Registration: browser automation fills the form
  - Ongoing: monitor for quarterly statements
- **SoundExchange** — digital performance royalties on sound recordings
  - Registration: browser automation fills the form
  - Ongoing: monitor for semi-annual payments

**Also tracks:**
- Whether a publishing entity is set up (needed to collect 50% of performance royalties)
- ISRC codes for each release
- Works registration with the PRO (each song registered individually)

**Schema changes:**
```sql
CREATE TABLE IF NOT EXISTS royalty_registrations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org_name        TEXT    NOT NULL,     -- 'ASCAP', 'BMI', 'MLC', 'SoundExchange'
    org_type        TEXT    NOT NULL,     -- 'PRO', 'mechanical', 'digital_performance'
    status          TEXT    DEFAULT 'not_started',
    -- not_started, recommended, approved_by_artist, in_progress, submitted,
    -- confirmed, active, monitoring
    account_id      TEXT,                -- artist's account/member ID once registered
    registered_at   TEXT,
    last_checked    TEXT,                -- last time agent checked the dashboard
    next_check_due  TEXT,                -- when to check again
    notes           TEXT,                -- agent notes on status, issues, amounts seen
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS works_registrations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id        INTEGER NOT NULL REFERENCES tracks(id),
    org_name        TEXT    NOT NULL,     -- which org this work is registered with
    registration_id TEXT,                -- the org's ID for this work
    status          TEXT    DEFAULT 'pending',
    -- pending, submitted, confirmed
    isrc            TEXT,
    iswc            TEXT,                -- International Standard Musical Work Code
    registered_at   TEXT,
    created_at      TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS royalty_news (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org_name        TEXT,
    headline        TEXT    NOT NULL,
    summary         TEXT,
    source_url      TEXT,
    relevance       TEXT,                -- why this matters to the artist
    flagged_by      TEXT    DEFAULT 'studio',
    created_at      TEXT    DEFAULT (datetime('now'))
);
```

**New MCP tools:**
- `recommend_registrations()` — Analyze artist's situation and recommend which orgs to register with
- `start_registration(org_name)` — Begin browser-automated registration (requires artist approval first)
- `check_registration_status()` — Return status of all registrations
- `monitor_royalty_dashboards()` — Log into each org's dashboard, check for new statements/payments
- `scan_royalty_news()` — Use Perplexity to search for relevant industry news (PRO rate changes, new collection opportunities, MLC deadlines)
- `register_work(track_id, org_name)` — Register a specific track/song with an org after release

**MCP integrations used:**
- `Claude_in_Chrome` / `playwright` — browser automation for form filling and dashboard monitoring
- `perplexity` — industry news scanning
- `google-calendar` — schedule recurring dashboard checks

**Agent changes:**
- Diane: owns the royalty registration workflow. After first release, initiates the recommendation. "you just put music into the world. there are three organizations that will pay you for it. here's what i recommend and why. say the word and i'll handle the paperwork"
- Studio: monitors dashboards on a schedule (weekly for PROs, monthly for MLC/SoundExchange)
- Studio: scans royalty news monthly, flags anything relevant to the artist

**Flow:**
1. Artist releases first track
2. Diane texts: "congrats on the release. now let's make sure you get paid. i recommend registering with [ASCAP/BMI], the MLC, and SoundExchange. want me to handle it?"
3. Artist says yes
4. Studio agent uses browser automation to navigate to each site, fill registration forms
5. At each form submission point, agent pauses and asks for confirmation
6. Once registered, agent stores account IDs and sets up monitoring schedule
7. Ongoing: agent checks dashboards, reports incoming royalties, flags issues
8. For each new release: agent registers the work with each org automatically

---

### 6. Album-as-Statement Mode (Prince via Rogers)

**What:** When starting an EP or album, the artist writes a thematic seed — who they are
right now and what this collection is trying to say. Every track is evaluated against
that seed. Prince removed "Moonbeam Levels" from three albums because it didn't fit.

**Why:** The difference between a playlist and an album is intent. A collection of
good songs is not the same as a statement. The seed forces the artist to decide
what the project IS before choosing what goes on it.

**Schema changes:**
```sql
ALTER TABLE projects ADD COLUMN thematic_seed TEXT;
ALTER TABLE projects ADD COLUMN seed_set_at TEXT;
```

**New MCP tools:**
- `set_project_seed(project_id, seed_text)` — Store the thematic seed for a project
- `evaluate_seed_alignment(track_id, project_id)` — Score how well a track serves the project's statement
- `get_project_coherence(project_id)` — Overview: which tracks fit the seed, which drift, which are missing pieces

**Agent changes:**
- Nico: when a project is created, asks "before we start — in a few sentences, what is this project about? not genre. what are you trying to say right now?" Feedback on tracks within a project includes seed-alignment notes
- Diane: tracks project coherence alongside her normal milestone tracking
- Mika: visual direction for an album era should also align with the seed

---

## Tier 2 — Build Soon (After First Release)

### 7. Ego-Off Collaboration Mode (Quincy's "Check Your Ego")

**What:** Agent behavior updates that encode Quincy Jones' collaboration principles.

**Changes:**
- Nico frames all critique as "what does the song need?" never "here's what I would do"
- When artist is in a revision spiral (4+ versions, diminishing progress), Nico triggers a "leave space" intervention: "stop adding. remove the last thing you put in. what's left?"
- The "God Walks Through The Room" rule: Nico suggests muting layers to find the essential core. Happy accidents (off-grid timing, unexpected harmonics) are preserved by default, never "fixed"
- Track which suggestions get adopted vs. rejected over time (via agent_memory). Adapt feedback style to what actually lands

### 8. Output Velocity Monitor (Prince-mode vs. D'Angelo-mode)

**What:** Diane watches the artist's creation rate and adapts her approach.

**Thresholds:**
- **< 1 track/month** — Prince-mode: daily nudge to create SOMETHING. "what's today's song?" Vault everything regardless of quality. The habit is the goal
- **1-4 tracks/month** — Healthy range. Normal operations
- **> 6 releases/year with declining engagement** — Curation-mode: "you're producing plenty. let's be choosier about what gets released. run the panel on your last 3"
- **In a deep multi-year project** — D'Angelo-mode: respect the process. Don't nag about output. Instead: Yoda-mode study sessions ("listen to these 3 reference tracks before your next session")
- **Yoda Mode** (D'Angelo/Questlove "Soul University"): before a recording session, Nico generates a study list of 3 reference tracks to analyze front-to-back. Not to copy — to calibrate

### 9. Distribution Beyond Bandcamp (DSP Delivery)

**What:** When catalog has 5+ releases, add multi-platform distribution.

**Integration:** DistroKid or Symphonic API (or browser automation for manual submission)

**Capabilities:**
- Submit to Spotify, Apple Music, Tidal, YouTube Music, Amazon Music
- Pre-save link generation (Linkfire, Show.co)
- Spotify for Artists editorial pitch (T-8 weeks before release)
- Rex handles this as extension of his role — Bandcamp remains home base, DSPs are reach

**MCP integrations:** Browser automation for DistroKid/Symphonic dashboards

### 10. Sync Pitching Pipeline

**What:** Use catalog intelligence (Feature #4) to actively pitch tracks for sync placement.

**Flow:**
1. Rex identifies sync-ready tracks (Tier A, complete metadata, clean masters)
2. Agent submits to That Pitch (free, 100+ libraries) via browser automation
3. Agent submits to Songtradr/Musicbed marketplace via browser automation
4. Gmail integration for direct outreach to music supervisors
5. Track all submissions and responses in a `sync_submissions` table
6. Monitor for placement confirmations and licensing offers

**Schema:**
```sql
CREATE TABLE IF NOT EXISTS sync_submissions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id        INTEGER NOT NULL REFERENCES tracks(id),
    platform        TEXT    NOT NULL,     -- 'that_pitch', 'songtradr', 'musicbed', 'direct'
    status          TEXT    DEFAULT 'submitted',
    -- submitted, under_review, placed, rejected, expired
    submission_date TEXT    DEFAULT (datetime('now')),
    response_date   TEXT,
    placement_details TEXT,              -- show/film/commercial details if placed
    fee             REAL,                -- licensing fee if applicable
    notes           TEXT
);
```

### 11. Marketing Campaign Planner

**What:** When a release is approaching, generate a basic rollout plan.

**Capabilities:**
- 8-week backward-planned content calendar
- Social post drafts (agent writes, artist approves, scheduled via Buffer/Later API or manual posting)
- Press outreach list generation (via Perplexity research + browser)
- Email newsletter draft for direct fans (via Gmail)
- Post-release sustained promotion for 4 weeks

**MCP integrations:** Gmail (press outreach), Google Calendar (content calendar), Perplexity (research press contacts), Browser (social media scheduling tools)

---

## Tier 3 — Build Later (When Scaling)

### 12. Financial Tracking / Royalty Accounting

- Track all incoming revenue by source (Bandcamp, streaming, sync, merch)
- Calculate per-release ROI
- Monthly/quarterly financial summaries
- Integration with Curve, Reprtoir, or simple spreadsheet via Google Sheets MCP
- Cash flow monitoring: ensure 3-month operating reserves

### 13. Contract Review Agent

- When artist signs any agreement, agent reviews terms via browser
- Flag non-standard terms: perpetuity grants, recoupment opacity, missing audit rights
- Track termination-right windows (35 years post-1978 Copyright Act)
- Recommend catalog acquisition when cashflow allows (the Jackson/Prince lesson)
- Human attorney signoff always required — agent surfaces issues, doesn't decide

### 14. Live Performance / Tour Routing

- Booking-Agent.io integration for venue discovery based on genre/fanbase data
- TourSmart for route optimization and local media identification
- Google Calendar integration for show dates
- Settlement tracking via browser automation (Eventbrite, See Tickets)
- Post-show data: ticket sales, merch revenue, audience size
- Music Mogul AI for contracts and promotional materials

### 15. Merch / D2C Store

- Fourthwall integration for print-on-demand (no inventory)
- Limited edition physical releases (vinyl via Qrates, cassette via Duplication.ca)
- Bandcamp physical add-ons (bundle digital + physical)
- Track merch revenue alongside music revenue

### 16. Publishing Administration

- Songtrust or Sentric API integration for global mechanical/performance collection
- Publishing entity setup guidance (needed for 50% of performance royalties)
- Sub-publishing in foreign territories
- Catalog tagging agent: Cyanite/Bridge.audio auto-tag for publisher databases

### 17. Multi-Artist Roster (if expanding to a label)

- Per-artist data isolation
- Transparent royalty ledger (prevent the Cash Money/Death Row failure modes)
- 50/50 net-profit deal templates with sunset/reversion clauses
- Per-artist development tracker (TDE boot camp model)
- Hard caps: 6 releases/year per A&R head (XL model)
- Maximum advance = 50% of trailing 12-month per-artist revenue

---

## Agent Behavior Changes Summary

### Nico (A&R) gains:
- Vault awareness — reference past vaulted tracks in feedback
- Panel results presentation — deliver human QC results with editorial framing
- Seed-alignment feedback when in EP/album mode
- "Leave space" intervention for over-arrangement (Quincy rule)
- "What does the song need?" framing (ego-off)
- Yoda Mode study list generation
- Sync tag generation alongside regular audio analysis

### Diane (Manager) gains:
- Release cycle auto-planner (T-minus countdown)
- Google Calendar integration for all deadlines
- Royalty registration initiation and tracking
- Output velocity monitoring (Prince-mode / D'Angelo-mode)
- Project-level thematic seed tracking
- Post-release royalty registration checklist
- Panel session coordination (timing, follow-up)

### Mika (Creative Director) gains:
- Era awareness (distinct visual identity per album/EP cycle)
- Brand consistency checking against established palette from memory
- Visual identity deck generation for each new era
- Artwork deadline tied to release cycle (T-4 weeks)

### Rex (Bandcamp & Release Ops) gains:
- Sync-readiness tag awareness
- Multi-platform distribution (Tier 2)
- Release page optimization tied to release cycle milestones
- Sync submission pipeline (Tier 2)
- ISRC code tracking

### Studio Conductor gains:
- Human QC Panel orchestration (send, monitor, summarize via iMessage)
- Release cycle state awareness
- Royalty dashboard monitoring (scheduled browser automation)
- Royalty news scanning (Perplexity)
- Cross-agent coordination during release windows
- Gmail draft review and approval routing
- Google Calendar event creation for milestones

---

## Build Order

### Sprint 1: Foundation wiring
- [ ] Connect Google Calendar MCP to Diane's profile
- [ ] Connect Gmail MCP to Rex and Diane's profiles
- [ ] Connect iMessage MCP to Studio conductor
- [ ] Connect browser automation to Studio conductor
- [ ] Connect Perplexity to all agents
- [ ] Test each integration end-to-end

### Sprint 2: Vault + Release Cycle
- [x] Schema migration: vault states, vault_reason, vault_date on tracks
- [x] MCP tools: vault_track, vault_search, resurface_track
- [x] Schema migration: due_date and milestone_type on milestones
- [x] MCP tools: plan_release, get_release_timeline
- [ ] Google Calendar integration for release milestones
- [x] Update Nico and Diane SOUL.md with vault + release cycle behaviors
- [ ] Test: create a release plan, verify calendar events created

### Sprint 3: Human QC Panel
- [x] Schema migration: listening_panel, panel_sessions, panel_responses tables
- [x] MCP tools: manage_panel, send_to_panel, log_panel_response, get_panel_results
- [ ] Desktop app: Panel management UI (add/remove contacts, view session history)
- [ ] iMessage integration: send audio files, monitor responses
- [x] Studio conductor: panel orchestration logic (SOUL.md updated)
- [ ] Test: add 2 panel members, send a track, collect responses

### Sprint 4: Sync Intelligence
- [x] Schema migration: sync columns on audio_analyses (sync_scene_tags, vocal_presence, explicit_content, sync_tier, isrc)
- [ ] Extend Gemini prompt to include sync-relevant analysis
- [ ] MCP tools: tag_for_sync, get_sync_ready_tracks, generate_sync_sheet
- [x] Update Rex's SOUL.md with sync readiness tagging behaviors
- [ ] Test: analyze a track, verify sync tags generated

### Sprint 5: Autonomous Royalty Registration
- [x] Schema migration: royalty_registrations, works_registrations, royalty_news tables
- [x] MCP tools: manage_royalty_registration, register_work
- [ ] Browser automation flows for ASCAP/BMI, MLC, SoundExchange registration
- [ ] Perplexity integration for royalty news scanning
- [ ] Google Calendar: schedule recurring dashboard checks
- [x] Diane SOUL.md: post-release royalty workflow
- [ ] Test: dry-run a registration flow (navigate forms without submitting)

### Sprint 6: Album-as-Statement Mode
- [x] Schema migration: thematic_seed, seed_set_at on projects
- [x] MCP tools: set_project_seed, get_project_coherence
- [x] Update Nico + Mika SOUL.md: seed-alignment feedback behavior
- [ ] Test: create a project with seed, evaluate track alignment

### Sprint 7: Agent Behavior Polish
- [ ] Ego-off collaboration mode updates to Nico's SOUL.md
- [ ] Output velocity monitoring logic for Diane
- [ ] Yoda Mode reference track generation for Nico
- [ ] Era awareness updates for Mika
- [ ] Brand consistency memory integration for Mika

### Sprint 8+: Tier 2 features
- [ ] Distribution beyond Bandcamp
- [ ] Sync pitching pipeline
- [ ] Marketing campaign planner
- [ ] Financial tracking
- [ ] Everything in Tier 3

---

## Principles (From the Research)

These principles guide every feature we build:

1. **Song-first quality gates.** A great song can make a star out of the worst singer in the world. Filter at the song level; everything downstream is execution.
2. **Volume then curation.** Generate freely, vault everything, curate ruthlessly for release. Prince wrote a song a day. Jones auditioned 800 to pick 9.
3. **Ownership is not optional.** Register everything. Own your masters. Own your publishing. The agents handle the paperwork so you never miss a dollar.
4. **Check your ego at the door.** Feedback is about what the song needs, not what anyone wants. Human panel feedback is honest because they're friends, not employees.
5. **The studio is the instrument.** The system IS the creative environment. It should reduce friction to zero — drop a file, everything else happens.
6. **Daily ritual over inspiration.** None of the five legends waited to feel inspired. The system nudges daily creation regardless of mood.
7. **Every release is part of a larger story.** Catalog thinking, not single thinking. Each release adds a coordinate to the map.
8. **Transparency prevents every financial failure mode.** Every royalty tracked, every registration confirmed, every dollar accounted for. This single principle prevents 80% of label failures.
9. **Human connection is irreplaceable.** Real listeners. Real reactions. AI handles logistics; humans handle taste.
10. **The artist makes music. The system handles everything else.**
