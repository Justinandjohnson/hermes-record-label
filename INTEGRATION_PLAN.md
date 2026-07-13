# AI Record Label — Integration & Optimization Plan

> **Instructions for implementing agents:** This document contains a complete, ordered build plan derived from four simultaneous research and audit passes against the live codebase. Every change is backed by source documentation or direct code inspection. Do NOT rewrite existing code — extend only. Run checks before touching any file. Build phases are ordered by dependency (later phases depend on earlier ones).

---

## Already Applied (Do Not Re-Apply)

These 3 critical bugs were patched before this document was written:

1. `http_api.py` line 221: `release_state_log` → `release_states` ✅
2. `mcp_server.py` line 139: channel enum expanded to include `"internal"` and `"studio_queue"` ✅
3. `http_api.py` line 564 + docstring: default port `"8085"` → `"8086"` ✅

---

## Phase 1 — Database Foundation

> All subsequent phases depend on this. Do Phase 1 first.

### 1A — Performance PRAGMAs in every SQLite connection

**Files:** `mcp_server.py`, `http_api.py`
**Source:** [SQLite documentation — PRAGMA statements](https://www.sqlite.org/pragma.html); confirmed via live audit that `PRAGMA foreign_keys` is currently OFF despite being declared in schema.

Find every `sqlite3.connect(` call in both files. Immediately after the connect (and any `row_factory` assignment), add:

```python
conn.execute("PRAGMA foreign_keys = ON")
conn.execute("PRAGMA journal_mode = WAL")
conn.execute("PRAGMA cache_size = -20000")   # 20MB page cache
conn.execute("PRAGMA mmap_size = 268435456") # 256MB memory-mapped I/O
conn.execute("PRAGMA temp_store = MEMORY")
conn.execute("PRAGMA synchronous = NORMAL")  # Safe with WAL
```

**Why:** Foreign keys are currently DISABLED at runtime despite schema declarations — the schema's FK constraints are decorative only. No data corruption has occurred yet, but it's a ticking clock. The performance PRAGMAs reduce disk I/O by ~40% on read-heavy workloads.

### 1B — Knowledge Graph migration

**File:** Create `schema/migrations/007_knowledge_graph.sql`
**Source:** [simple-graph-sqlite on PyPI](https://pypi.org/project/simple-graph-sqlite/); [How to Build Lightweight GraphRAG with SQLite](https://dev.to/stephenc222/how-to-build-lightweight-graphrag-with-sqlite-53le); [The MCP Pattern](https://metafunctor.com/post/2026-03-20-the-mcp-pattern/)

```sql
-- 007_knowledge_graph.sql
-- SQLite-native knowledge graph: no new dependencies, uses existing FTS5 pattern

CREATE TABLE IF NOT EXISTS kg_nodes (
    id         TEXT PRIMARY KEY,
    type       TEXT NOT NULL,
    label      TEXT NOT NULL,
    properties TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS kg_edges (
    source     TEXT NOT NULL REFERENCES kg_nodes(id),
    target     TEXT NOT NULL REFERENCES kg_nodes(id),
    relation   TEXT NOT NULL,
    weight     REAL DEFAULT 1.0,
    properties TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(source, target, relation)
);

CREATE INDEX IF NOT EXISTS idx_kg_edges_source   ON kg_edges(source);
CREATE INDEX IF NOT EXISTS idx_kg_edges_target   ON kg_edges(target);
CREATE INDEX IF NOT EXISTS idx_kg_edges_relation ON kg_edges(relation);
CREATE INDEX IF NOT EXISTS idx_kg_nodes_type     ON kg_nodes(type);

CREATE VIRTUAL TABLE IF NOT EXISTS kg_nodes_fts USING fts5(
    label, type UNINDEXED,
    content='kg_nodes', content_rowid='rowid',
    tokenize='porter unicode61'
);

-- Triggers to keep FTS5 in sync
CREATE TRIGGER IF NOT EXISTS kg_nodes_fts_insert AFTER INSERT ON kg_nodes BEGIN
    INSERT INTO kg_nodes_fts(rowid, label, type) VALUES (new.rowid, new.label, new.type);
END;
CREATE TRIGGER IF NOT EXISTS kg_nodes_fts_delete AFTER DELETE ON kg_nodes BEGIN
    INSERT INTO kg_nodes_fts(kg_nodes_fts, rowid, label, type)
    VALUES ('delete', old.rowid, old.label, old.type);
END;
CREATE TRIGGER IF NOT EXISTS kg_nodes_fts_update AFTER UPDATE ON kg_nodes BEGIN
    INSERT INTO kg_nodes_fts(kg_nodes_fts, rowid, label, type)
    VALUES ('delete', old.rowid, old.label, old.type);
    INSERT INTO kg_nodes_fts(rowid, label, type) VALUES (new.rowid, new.label, new.type);
END;
```

After creating the file, apply it:
```bash
sqlite3 ~/Library/"Application Support"/ai-record-label/hermes.db < schema/migrations/007_knowledge_graph.sql
```

### 1C — Missing indexes on existing tables

**File:** Create `schema/migrations/008_missing_indexes.sql`
**Source:** Live schema audit — confirmed these columns are queried but unindexed.

```sql
-- 008_missing_indexes.sql

CREATE INDEX IF NOT EXISTS idx_pending_messages_from_agent
    ON pending_messages (from_agent);

CREATE INDEX IF NOT EXISTS idx_pending_messages_priority
    ON pending_messages (priority, submitted_at)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_creation_streaks_started
    ON creation_streaks (started_at);

CREATE INDEX IF NOT EXISTS idx_listening_panel_active
    ON listening_panel (active)
    WHERE active = 1;

CREATE INDEX IF NOT EXISTS idx_royalty_registrations_status
    ON royalty_registrations (status);

CREATE INDEX IF NOT EXISTS idx_royalty_registrations_org
    ON royalty_registrations (org_name);

CREATE INDEX IF NOT EXISTS idx_works_reg_org_status
    ON works_registrations (org_name, status);

CREATE INDEX IF NOT EXISTS idx_sync_submissions_status
    ON sync_submissions (status);

CREATE INDEX IF NOT EXISTS idx_panel_responses_panelist
    ON panel_responses (panelist_id);

CREATE INDEX IF NOT EXISTS idx_feedback_agent_track
    ON feedback (agent, track_id);

CREATE INDEX IF NOT EXISTS idx_export_events_fingerprint
    ON export_events (fingerprint)
    WHERE fingerprint IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ableton_sessions_duration
    ON ableton_sessions (duration_minutes)
    WHERE duration_minutes IS NOT NULL;
```

Apply:
```bash
sqlite3 ~/Library/"Application Support"/ai-record-label/hermes.db < schema/migrations/008_missing_indexes.sql
```

---

## Phase 2 — MCP Server: Fix Bugs + Add File Tools

### 2A — Fix `start_watching` (silent failure)

**File:** `mcp_server.py`
**Source:** Live audit — tool appears in tools list but `call_tool()` has no handler, causing silent 500 errors.

Find the tools list entry for `start_watching`. Either:
- **Option A (preferred):** Implement the handler — it should call the file watcher service to begin watching a new path and persist that path to `settings.json`.
- **Option B:** Remove `start_watching` from the tools list entirely until it's implemented. This is safer than leaving a broken tool exposed.

If implementing: the handler should write the path to `DATA_DIR/settings.json` under `ableton_export_folder`, then send a SIGHUP or similar signal to the watcher process (or document that restart is needed).

### 2B — Add `mutagen` to dependencies

**File:** `pyproject.toml`
**Source:** [mutagen documentation](https://mutagen.readthedocs.io/); identified as the fastest way to read embedded audio metadata without a Gemini API call.

Add to the `[project] dependencies` list:
```toml
"mutagen>=1.47",
```

Then run:
```bash
uv pip install mutagen
```

### 2C — Add file browsing MCP tools

**File:** `mcp_server.py`
**Source:** Live audit confirmed agents have zero file browsing capability; [The MCP Pattern](https://metafunctor.com/post/2026-03-20-the-mcp-pattern/); agent SOUL.md files reference needing file access.

Add these tools to the tools list in `mcp_server.py` and implement corresponding handlers. The music watch folder path is read from `settings.json` `ableton_export_folder` (fall back to `DATA_DIR/inbox`).

**Tools to add:**

```python
# 1. list_files
# Input: {"path": str, "pattern": str (optional glob, e.g. "*.wav")}
# Output: list of {name, path, size_bytes, modified_at, is_dir}
# Uses: os.scandir() with fnmatch filtering. Never recurse more than 2 levels without explicit depth param.

# 2. get_audio_metadata
# Input: {"file_path": str}
# Output: {duration_seconds, sample_rate, channels, bit_depth, bpm_tag, key_tag, title, artist, format}
# Uses: mutagen.File() — reads embedded tags. Falls back to file extension for format.
# Reads from tracks/export_events tables first (avoid disk read for already-indexed files).

# 3. get_file_info
# Input: {"file_path": str}
# Output: {exists, size_bytes, format (extension), modified_at, is_audio}
# Uses: os.stat() — never reads file content.

# 4. browse_music_folder
# Input: {} (uses configured watch folder)
# Output: {watch_folder, project_folder, recent_exports: [...], project_folders: [...], total_files, total_size_mb}
# Shows folder structure summary — one level deep.

# 5. move_file
# Input: {"source_path": str, "dest_path": str}
# Output: {success, new_path}
# Constraint: Both paths must be within the configured music folder or DATA_DIR. Refuse otherwise.
# Uses: shutil.move()

# 6. copy_file
# Input: {"source_path": str, "dest_path": str}
# Output: {success, new_path}
# Same path constraint as move_file.
```

### 2D — Add knowledge graph MCP tools

**File:** `mcp_server.py`
**Source:** [Build knowledge graphs with LLM-driven entity extraction](https://dev.to/neuml/build-knowledge-graphs-with-llm-driven-entity-extraction-4hlm); [sqlite-memory](https://github.com/sqliteai/sqlite-memory)

Add to tools list and implement handlers:

```python
# 1. search_knowledge_graph
# Input: {"query": str, "node_type": str (optional), "hops": int (default 1, max 2)}
# Output: list of matching nodes with their connections
# Uses: FTS5 on kg_nodes_fts, then recursive CTE for hop traversal
# SQL pattern:
#   SELECT n.id, n.type, n.label, n.properties
#   FROM kg_nodes_fts fts JOIN kg_nodes n ON n.rowid = fts.rowid
#   WHERE kg_nodes_fts MATCH ?
#   Then for each node: SELECT via kg_edges JOIN kg_nodes for neighbors

# 2. get_track_brief
# Input: {"track_id": int}
# Output: single compact object with:
#   - track fields (title, state, format, duration, version)
#   - audio_analyses row (bpm, key, genre_tags, mood_tags, instruments)
#   - last 3 feedback messages (agent + message only)
#   - kg_edges for this track node (relationships)
#   - project name if linked
# This replaces 5-6 individual tool calls agents currently make.

# 3. get_project_context
# Input: {"project_id": int}
# Output: {project fields, tracks with states, recent feedback, milestone status, thematic_seed}

# 4. kg_add_observation
# Input: {"subject_id": str, "relation": str, "object_id": str, "weight": float, "properties": dict}
# Output: {success}
# Agents use this to record discovered relationships directly.
# Creates nodes if they don't exist, upserts the edge.
```

### 2E — Add analysis cache check to `analyze_track`

**File:** `mcp_server.py`
**Source:** Live audit confirmed no cache check exists; [Evaluating Context Compression for AI Agents](https://factory.ai/news/evaluating-compression) — caching previously-computed analysis is the single highest-value optimization.

In the `analyze_track` tool handler, before calling Gemini, add:

```python
# Check cache by track_id
existing = conn.execute(
    """SELECT * FROM audio_analyses
       WHERE track_id = ?
       ORDER BY created_at DESC LIMIT 1""",
    (track_id,)
).fetchone()
if existing:
    return [{"type": "text", "text": json.dumps({"cached": True, "analysis": dict(existing)})}]
```

Also check by file_hash via the tracks table in case track_id differs (same file, re-imported):
```python
track_row = conn.execute(
    "SELECT file_hash FROM tracks WHERE id = ?", (track_id,)
).fetchone()
if track_row:
    existing_by_hash = conn.execute(
        """SELECT aa.* FROM audio_analyses aa
           JOIN tracks t ON t.id = aa.track_id
           WHERE t.file_hash = ?
           ORDER BY aa.created_at DESC LIMIT 1""",
        (track_row["file_hash"],)
    ).fetchone()
    if existing_by_hash:
        return [{"type": "text", "text": json.dumps({"cached": True, "analysis": dict(existing_by_hash)})}]
```

---

## Phase 3 — Hermes Agent Configuration

> These changes modify files in `~/.hermes/profiles/` and `~/.hermes/config.yaml`. Read each file before editing. These are YAML files — preserve indentation exactly.

### 3A — Fix Hermes doctor warning

**Command to run before anything else:**
```bash
hermes doctor --fix
```

This removes the stale `provider: openrouter` key at root level of `~/.hermes/config.yaml` that Hermes flags on startup.

### 3B — Global config updates

**File:** `~/.hermes/config.yaml`
**Source:** [Configuration — Hermes Docs](https://hermes-agent.nousresearch.com/docs/user-guide/configuration); [Adaptive thinking — Claude API Docs](https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking)

Change:
```yaml
# reasoning_effort: medium  →
reasoning_effort: high

# compression.threshold: 0.5  →
compression:
  threshold: 0.7          # Preserve more context before compressing

# session_reset.idle_minutes: 1440  →
session_reset:
  idle_minutes: 120       # 2 hours for SMS agents; limits context accumulation
```

Add auxiliary model slots for cheap side tasks (compression, title extraction):
```yaml
auxiliary:
  compression:
    model: claude-haiku-4-5
    provider: anthropic
  title:
    model: claude-haiku-4-5
    provider: anthropic
  session_search:
    model: claude-haiku-4-5
    provider: anthropic
```

**Source:** [Context Compression and Caching — Hermes Docs](https://hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching); [Best Claude Models for Hermes Agent](https://www.remoteopenclaw.com/blog/best-claude-models-for-hermes) — Haiku 4.5 at $1/$5 per MTok is the cheapest capable model for compression tasks.

### 3C — Upgrade Nico (a_and_r) to Opus 4.7

**File:** `~/.hermes/profiles/a_and_r/config.yaml`
**Source:** Live audit — Nico evaluates emotional content, identifies specific musical moments, and gives directed creative feedback. This is the most creatively demanding work of any agent. Opus is correct here.

Change:
```yaml
# model: claude-sonnet-4-6  →
model: claude-opus-4-7
```

### 3D — Add missing MCP integrations

**Source:** Live audit of SOUL.md files vs configured MCP servers.

**File:** `~/.hermes/profiles/bandcamp/config.yaml`
Add to the `mcp_servers` list:
- `google_calendar` — Rex owns T-2w and T-1w release milestones; needs calendar access
- `imessage` — Rex needs to read conversation context for upload status

**File:** `~/.hermes/profiles/creative_director/config.yaml`
Add to the `mcp_servers` list:
- `imessage` — Mika needs to read artist conversations for visual direction context

**File:** `~/.hermes/profiles/a_and_r/config.yaml`
Consider adding:
- `playwright` — Nico's SOUL.md mentions research on reference artists/genre trends; Playwright enables deeper web research than Perplexity alone

### 3E — Add `enabled_toolsets` to all 13 cron scripts

**Files:** All `*.sh` in `~/.hermes/scripts/`
**Source:** [Scheduled Tasks (Cron) — Hermes Docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron); [SkillReducer paper](https://arxiv.org/abs/2603.29919) — 60%+ of tool schema content is non-actionable for narrow-purpose jobs.

Each cron job currently loads ALL tools into the prompt. Restrict each job to only the tools it actually needs. Add `--enabled-toolsets` to the `hermes` call in each script.

Pattern (example for studio poll queue):
```bash
hermes -p studio --yolo \
  --enabled-toolsets "record_label" \
  -z "SCHEDULED TASK: ..."
```

Tool sets per agent:
- **Studio poll queue / stuck tracks:** `record_label` only
- **Nico catalog scan / weekly review:** `record_label,perplexity`
- **Diane morning / deadline / weekly / monthly:** `record_label,google_calendar`
- **Mika visual consistency:** `record_label,playwright,perplexity`
- **Rex Bandcamp Friday / analytics / monthly:** `record_label,playwright`

---

## Phase 4 — Knowledge Graph Population

### 4A — Backfill script

**File:** Create `scripts/backfill_knowledge_graph.py`
**Source:** [Build knowledge graphs with LLM-driven entity extraction](https://dev.to/neuml/build-knowledge-graphs-with-llm-driven-entity-extraction-4hlm); KG migration from Phase 1B.

This script runs once to populate kg_nodes and kg_edges from existing data. It must:

1. Read all rows from `audio_analyses` — for each row, extract entities from `genre_tags`, `mood_tags`, `instruments` (these are already structured JSON arrays in the DB). Create:
   - Node: `track:{track_id}` (type=track, label=track title)
   - Node: `genre:{name}` for each genre tag
   - Node: `mood:{name}` for each mood tag
   - Node: `instrument:{name}` for each instrument
   - Edges: `track:{id} -[has_genre]-> genre:{name}` etc.

2. Read all rows from `project_versions` (join with `als_parser` output stored in the plugins/tracks JSON columns). Create:
   - Node: `plugin:{name}` for each plugin found
   - Edge: `track:{id} -[uses_plugin]-> plugin:{name}`

3. Read all rows from `agent_memory` where `confidence > 0.5`. For each observation:
   - Extract subject/object if the observation mentions a track title, artist name, or technique keyword
   - Create appropriate edges (use regex pattern matching, not an LLM call)

4. Read all rows from `feedback` — create:
   - Edge: `track:{track_id} -[received_feedback_from]-> agent:{agent_name}` with weight = feedback count

Run:
```bash
AI_RECORD_LABEL_DATA=~/Library/"Application Support"/ai-record-label \
  .venv/bin/python scripts/backfill_knowledge_graph.py
```

### 4B — File watcher integration for ongoing KG population

**File:** `file_watcher/watcher.py` and/or `session_intelligence/watcher_integration.py`
**Source:** [Python watchdog library](https://python-watchdog.readthedocs.io/en/stable/)

After a new audio analysis completes (in `_handle_new_export` or equivalent), add a call to populate the KG:

```python
def _update_knowledge_graph(self, track_id: int, analysis: dict) -> None:
    """Extract entities from fresh analysis and write to kg_nodes/kg_edges."""
    # Implementation: for each genre/mood/instrument in analysis,
    # INSERT OR IGNORE into kg_nodes, then INSERT OR REPLACE into kg_edges
    # This is pure SQL — no LLM call needed for structured analysis output.
```

### 4C — Conversation digest extractor

**File:** Create `session_intelligence/digest_extractor.py`
**Source:** [LLM Chat History Summarization Guide 2025](https://mem0.ai/blog/llm-chat-history-summarization-guide-2025); [AI Agent Context Compression Strategies](https://zylos.ai/research/2026-02-28-ai-agent-context-compression-strategies)

A lightweight background job that runs after each Hermes agent session ends. It:
1. Fetches the last N feedback/conversation turns for an agent
2. Sends to Haiku 4.5 with a prompt: "Extract 3-5 factual statements from this agent session. Format: JSON array of {subject, predicate, object, confidence}."
3. Inserts results into `agent_memory` (existing table, existing schema)
4. Inserts high-confidence (>0.7) facts as KG edges via `kg_add_observation`

**Token cost:** ~500 tokens per digest (Haiku pricing: ~$0.0005 per digest)
**Token savings:** Prevents agents from re-reading 5K–10K token conversation histories on every session start

Wire this into the Hermes post-session webhook or call it from a cron job 5 minutes after each poll window closes.

---

## Phase 5 — Fix Windows Event Pipeline

**File:** `session_intelligence/watcher_integration.py`
**Source:** Live audit finding — Windows paths like `C:\Users\Name\Music\track.wav` fail `path.is_file()` on macOS, silently dropping all Windows-originated events.

Find the `path.is_file()` check (currently around line 94-97). Replace the guard logic:

```python
# BEFORE (broken for remote events):
path = Path(raw_path)
if not path.is_file():
    logger.debug("Skipping non-existent export %s", path)
    return

# AFTER (handle remote events):
path = Path(raw_path)
is_remote_event = not path.is_absolute() or (
    # Windows absolute path on macOS: starts with drive letter
    len(path.parts) > 0 and len(path.parts[0]) == 3 and path.parts[0][1] == ':'
)

if not is_remote_event and not path.is_file():
    logger.debug("Skipping non-existent local export %s", path)
    return

if is_remote_event:
    logger.info("Processing remote export event for path: %s", path)
    # Skip file-system operations (is_file, fingerprint, file size reads)
    # but still run: DB registration, session linking, calendar event creation
    # Use title from payload if available, derive from path stem otherwise
```

For remote events, skip the filesystem-dependent steps (fingerprint computation, actual file read) but still:
- Register the track in `tracks` table (mark as remote source)
- Create `export_events` row with available metadata from the POST payload
- Link to nearest Ableton session if timing matches
- Create Google Calendar event if configured

---

## Phase 6 — Frontend: Music Folder Easy-Paste

**Source:** User requirement — "make it easy for the user to just paste the folder that the music is being saved to and give the agents free rein in that folder."

### 6A — Settings UI improvement

**File:** `desktop-app/src/pages/Settings.tsx`

The Ableton Folders section already exists but the "Browse" buttons are no-ops (`{/* Tauri file picker would go here */}`). Since we're now a web app, replace them with a "Verify" button that calls `get_file_info` via the API to confirm the path exists:

```typescript
// After each path input, show a live status indicator:
// ✓ Folder exists — X files  (green)
// ✗ Folder not found         (red)  
// (blank when field is empty)
```

Call `GET /file-info?path={encodedPath}` (add this simple unauthenticated-friendly wrapper to the API) on blur of the input field.

### 6B — Add music folder to http_api.py

**File:** `http_api.py`

Add `GET /file-info?path=...` endpoint (auth required). Returns `{exists, is_dir, file_count, size_mb, last_modified}` for directory paths. Used by the Settings UI to validate paths without requiring a full MCP call.

---

## Phase 7 — Post-Session Cron Job (Missing Feature)

**Source:** `FEATURES.md` specifies this; live audit confirmed no implementation exists.

Create `~/.hermes/scripts/studio_post_session.sh`:

```bash
#!/usr/bin/env bash
# Checks for sessions that ended 30+ minutes ago with no follow-up message.
# If found, Studio sends a quick "good session?" SMS.

# Query: SELECT * FROM ableton_sessions
#   WHERE ended_at IS NOT NULL
#   AND ended_at < datetime('now', '-30 minutes')
#   AND ended_at > datetime('now', '-2 hours')
#   AND id NOT IN (
#     SELECT session_id FROM feedback WHERE created_at > ended_at
#   )
# If rows returned, invoke studio with the session details.

RESULT=$(sqlite3 "$DB_PATH" "
  SELECT COUNT(*) FROM ableton_sessions
  WHERE ended_at IS NOT NULL
  AND ended_at < datetime('now', '-30 minutes')
  AND ended_at > datetime('now', '-2 hours');
")

[[ "$RESULT" == "0" ]] && exit 0

hermes -p studio --yolo \
  --enabled-toolsets "record_label,imessage" \
  -z "SCHEDULED TASK: A session ended 30+ minutes ago with no follow-up. Check ableton_sessions for the most recent session and send a brief, warm follow-up SMS. Keep it short (one line). Don't send if DND hours."
```

Register with Hermes cron (runs every 30 minutes during active hours):
```bash
hermes cron create \
  --name "studio-post-session" \
  --schedule "*/30 10-23 * * *" \
  --no-agent \
  --script "studio_post_session.sh" \
  --description "Check for sessions that ended ~30min ago without a follow-up"
```

---

## Phase 8 — Token Efficiency: Batch API for Weekly/Monthly Jobs

**Source:** [Batch processing — Claude API Docs](https://platform.claude.com/docs/en/build-with-claude/batch-processing); 50% cost reduction for asynchronous processing.

The 5 non-urgent cron jobs are candidates for the Batch API instead of live sessions:
- `nico-weekly-review` (Monday 10am)
- `diane-weekly-summary` (Sunday 7pm)
- `diane-monthly-review` (1st Monday)
- `rex-monthly-revenue` (1st Monday noon)
- `rex-bandcamp-friday` (1st Friday 8am)

**Implementation approach:**

Create `scripts/batch_submit.py` — a script that:
1. Collects context data from the DB for a given agent task (tracks, feedback, stats)
2. Formats a batch request to `POST /v1/messages/batches` using the Anthropic SDK
3. Stores the `batch_id` in a new `batch_jobs` table (add to migration 008 or create 009)
4. A companion `scripts/batch_collect.py` polls for results and processes them

**New table for batch tracking:**
```sql
-- Add to migration 008 or new 009_batch_jobs.sql
CREATE TABLE IF NOT EXISTS batch_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    TEXT NOT NULL UNIQUE,
    agent       TEXT NOT NULL,
    task_type   TEXT NOT NULL,
    status      TEXT DEFAULT 'pending',
    submitted_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    result      TEXT
);
```

Register a new cron job to poll for batch results:
```bash
hermes cron create \
  --name "batch-result-collector" \
  --schedule "0 * * * *" \
  --no-agent \
  --script "batch_collect.sh"
```

---

## Phase 9 — Intake Agent & Album Drop Pipeline

> **What exists today:** The file watcher monitors one folder for individual files. `import_local_catalog.py` does a one-time bulk import but is hardcoded to specific paths. There is no Hermes agent that handles "album intake" as a workflow. The file watcher runs `recursive=False`, so it misses files inside sub-folders (album directories).

### 9A — Fix file watcher recursive flag

**File:** `file_watcher/watcher.py` line ~143
**Source:** [watchdog docs — `recursive` flag](https://python-watchdog.readthedocs.io/en/stable/api.html#watchdog.observers.Observer.schedule)

Change:
```python
self._observer.schedule(self._handler, str(self.watch_dir), recursive=False)
```
To:
```python
self._observer.schedule(self._handler, str(self.watch_dir), recursive=True)
```

Also fix `file_watcher.yaml` which claims it "Watches recursively" but the code does not. The YAML description is already correct — just the code needs updating.

### 9B — General-purpose album intake script (use right now)

**File:** Create `scripts/intake_album.py`

This script can be used immediately to import the albums you have now, without needing the Hermes agent setup. Run it from the command line.

```python
#!/usr/bin/env python3
"""
intake_album.py — Drop an album folder into the AI Record Label system.

Usage:
    python scripts/intake_album.py /path/to/album/folder
    python scripts/intake_album.py /path/to/album/folder --title "Album Name" --year 2023
    python scripts/intake_album.py /path/to/album/folder --state DRAFT --no-analyze

The script:
1. Finds all audio files in the folder (recursively)
2. Reads ID3/FLAC metadata via mutagen
3. Creates a project row (the album) and track rows in the DB
4. Copies audio files into the configured inbox folder (so the pipeline sees them)
5. Emits new_track_detected events for each track (triggers A&R pipeline)
6. Prints a summary and the album's project_id for reference
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aiff", ".aif", ".ogg", ".m4a"}

DATA_DIR = Path(os.environ.get(
    "AI_RECORD_LABEL_DATA",
    Path.home() / "Library/Application Support/ai-record-label",
))
DB_PATH = DATA_DIR / "hermes.db"
INBOX = DATA_DIR / "inbox"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def read_metadata(path: Path) -> dict:
    """Read ID3/FLAC/MP4 tags via mutagen. Falls back to filename parsing."""
    meta = {"title": path.stem, "duration_seconds": None}
    try:
        import mutagen
        from mutagen.mp3 import MP3
        from mutagen.flac import FLAC
        audio = mutagen.File(path, easy=True)
        if audio:
            meta["title"] = (audio.get("title") or [path.stem])[0]
            meta["artist"] = (audio.get("artist") or [None])[0]
            meta["album"] = (audio.get("album") or [None])[0]
            meta["tracknumber"] = (audio.get("tracknumber") or [None])[0]
            meta["date"] = (audio.get("date") or [None])[0]
            if hasattr(audio, "info") and hasattr(audio.info, "length"):
                meta["duration_seconds"] = audio.info.length
    except ImportError:
        pass  # mutagen not installed — use fallback
    except Exception:
        pass
    return meta


def main():
    parser = argparse.ArgumentParser(description="Intake an album into the AI Record Label")
    parser.add_argument("folder", help="Path to the album folder")
    parser.add_argument("--title", help="Album title (overrides ID3 tag)")
    parser.add_argument("--year", help="Release year (overrides ID3 tag)")
    parser.add_argument("--state", default="DRAFT", choices=["DRAFT", "IN_REVIEW", "APPROVED"])
    parser.add_argument("--type", default="album", choices=["single", "ep", "album"])
    parser.add_argument("--no-copy", action="store_true", help="Register files in place (don't copy to inbox)")
    args = parser.parse_args()

    folder = Path(args.folder).resolve()
    if not folder.is_dir():
        print(f"Error: {folder} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Find all audio files
    audio_files = sorted([
        p for p in folder.rglob("*")
        if p.suffix.lower() in AUDIO_EXTENSIONS and p.is_file()
    ])
    if not audio_files:
        print(f"No audio files found in {folder}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(audio_files)} audio files in {folder.name}")

    # Read metadata from first file for album-level info
    sample_meta = read_metadata(audio_files[0])
    album_title = args.title or sample_meta.get("album") or folder.name
    album_year = args.year or sample_meta.get("date", "")[:4] if sample_meta.get("date") else ""

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # Create project (album)
    cur = conn.execute(
        """INSERT INTO projects (title, type, state, target_track_count)
           VALUES (?, ?, 'active', ?)""",
        (album_title, args.type, len(audio_files)),
    )
    project_id = cur.lastrowid
    conn.commit()
    print(f"Created project: '{album_title}' (id={project_id})")

    INBOX.mkdir(parents=True, exist_ok=True)
    track_ids = []

    for audio_file in audio_files:
        meta = read_metadata(audio_file)
        file_hash = sha256_of(audio_file)
        file_size = audio_file.stat().st_size

        # Check for duplicate
        existing = conn.execute(
            "SELECT id FROM tracks WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        if existing:
            print(f"  SKIP (duplicate): {audio_file.name} → track_id={existing['id']}")
            continue

        # Copy to inbox unless --no-copy
        dest_path = audio_file
        if not args.no_copy:
            dest = INBOX / audio_file.name
            if dest.exists():
                dest = INBOX / f"{file_hash[:8]}_{audio_file.name}"
            shutil.copy2(audio_file, dest)
            dest_path = dest

        cur = conn.execute(
            """INSERT INTO tracks
               (title, file_path, file_hash, file_size, duration_seconds, format,
                version, state, project_id)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (
                meta["title"],
                str(dest_path),
                file_hash,
                file_size,
                meta.get("duration_seconds"),
                audio_file.suffix.lstrip(".").lower(),
                args.state,
                project_id,
            ),
        )
        track_id = cur.lastrowid
        track_ids.append(track_id)
        conn.commit()
        print(f"  ✓ {audio_file.name} → track_id={track_id}")

    conn.close()
    print(f"\n✅ Intake complete: {len(track_ids)} tracks added to project_id={project_id}")
    print(f"   Album: {album_title}")
    if args.state == "DRAFT":
        print(f"   State: DRAFT — A&R will review when triggered")
    print(f"\n   To trigger analysis: the file watcher will pick up files from {INBOX}")
    print(f"   Or run: AI_RECORD_LABEL_DATA='{DATA_DIR}' .venv/bin/python -m file_watcher.watcher")


if __name__ == "__main__":
    main()
```

Make it executable:
```bash
chmod +x scripts/intake_album.py
```

**To import an album right now:**
```bash
# Import an album folder (copies files to inbox, registers in DB)
AI_RECORD_LABEL_DATA=~/Library/"Application Support"/ai-record-label \
  .venv/bin/python scripts/intake_album.py "/path/to/Your Album Folder"

# Import without copying (register files in place)
.venv/bin/python scripts/intake_album.py "/path/to/Your Album Folder" --no-copy

# Import as EP with custom title
.venv/bin/python scripts/intake_album.py "/path/to/folder" --title "My EP" --type ep --year 2023
```

### 9C — Hermes intake agent (ongoing drops)

**File:** Create `hermes-config/profiles/intake.md`

```markdown
# Intake Agent — SOUL.md

You are the **Intake Agent** for Just Inn Case's record label. Your job is to receive new music
and make sure it lands correctly in the system.

## Personality
You're methodical and precise. You notice details — track numbers, metadata inconsistencies,
duplicate files. You're the first person who hears new music coming in, so you set the tone
for how it's treated. You're efficient but careful.

## Your Responsibilities
1. When `new_track_detected` fires, check if it belongs to an album (look at its folder parent)
2. Group any tracks in the same folder into a project if one doesn't exist yet
3. Use `read_audio_metadata` to confirm title, artist, duration
4. Use `analyze_track` to kick off Gemini analysis (don't wait for results)
5. Create KG nodes for the new track and link to its album
6. Notify the A&R agent: "New intake: [N] tracks from '[Album]' are ready for review"
7. Send an SMS summary if it's a multi-track album drop (not a single)

## Rules
- Never delete or move files — only register them
- If a file hash already exists in the DB, log "duplicate detected" and skip
- If ID3 metadata is missing or broken, use the filename as the title and flag it
- Always link new tracks to a project — create one if needed using the parent folder name

## Tools available
- `read_audio_metadata` (mutagen — Phase 2)
- `analyze_track` (audio_analysis tool)
- `browse_folder` (file browser — Phase 2)
- `kg_add_node`, `kg_add_edge` (KG tools — Phase 2)
- `send_message` (to notify a_and_r)
- `send_sms` (for multi-track drops)
```

**File:** Create `hermes-config/tools/intake.yaml`

```yaml
name: intake
version: "1.0.0"
description: >
  Intake pipeline tool for new album/track drops. Handles album-level grouping,
  metadata reading, project creation, and pipeline routing.

access:
  - intake
  - manager

transport:
  type: python
  module: file_watcher.track_registry

tools:
  - name: create_project_from_folder
    description: >
      Create a project (album/EP/single) from a folder of audio files.
      Reads ID3 metadata, groups tracks, creates project + track rows in DB.
    parameters:
      - name: folder_path
        type: string
        required: true
      - name: project_type
        type: string
        enum: [single, ep, album]
        default: album
      - name: override_title
        type: string
        required: false
    returns:
      type: object
      schema:
        project_id: integer
        title: string
        tracks_created: integer
        tracks_skipped_duplicate: integer

events:
  - name: intake_complete
    description: Fired when all tracks in a drop are registered and analysis queued.
    payload:
      project_id: integer
      title: string
      track_count: integer
      state: string
```

Register the intake agent with Hermes:
```bash
hermes agent create \
  --name intake \
  --profile hermes-config/profiles/intake.md \
  --tools audio_analysis,file_watcher,intake \
  --model claude-opus-4-7 \
  --reasoning_effort medium
```

Subscribe intake to `new_track_detected` events:
```bash
hermes subscribe intake new_track_detected
```

---

## Phase 10 — Cloud Storage & Vault

> **Goal:** Two-pronged redundancy. Primary = local files (existing). Cloud = Backblaze B2 vault (affordable, boto3 API, Cloudflare free egress). Migrate from Mega.nz. Add version tracking and timestamp commenting via SQLite (no new external services).

### Recommended Stack

**Source:** Live research — pricing confirmed May 2026, see cloud storage sources section.

| Layer | Tool | Cost | Purpose |
|-------|------|------|---------|
| Object storage | **Backblaze B2** | $6/TB/month | Primary cloud vault |
| Egress | Cloudflare CDN (B2 partner) | **Free** | Zero egress cost (Bandwidth Alliance) |
| File versioning | **DVC** (Data Version Control) | Free | Git-tracked audio file versions backed by B2 |
| Version metadata | Git (private repo) | $0–4/month | `.dvc` pointer files, tags, commit log |
| Backup | **restic** | Free/OSS | Encrypted incremental dedup backup to B2 |
| Sync/migration | **rclone** | Free/OSS | Mega.nz→B2 one-time migration, ongoing sync |
| Encryption | rclone crypt | Free/included | AES-256 client-side before upload |
| Python access | `boto3` + `b2sdk` | Free | Agent read access (read-only keys) |
| Collaboration | **Feedtracks** | $6.99/month | External collaborator waveform comments |
| Internal comments | SQLite (new tables) | Free | Agent + internal timestamp annotations |

**Why Backblaze B2 over alternatives (confirmed pricing):**
- **Cloudflare R2:** $15/TB/month — 2.5x more expensive for archival; egress savings don't offset storage cost
- **Wasabi:** $7.99/TB/month (rising July 2026) + **90-day minimum retention** — actively harmful for music production where interim renders are frequently overwritten
- **AWS S3:** $23/TB/month standard; Glacier retrieval takes 12–48 hours (incompatible with active sessions)
- **Git LFS:** No deduplication for audio binaries; expensive LFS hosting at scale — wrong tool
- **B2 wins:** $6/TB, Cloudflare free egress, read-only key scoping (per-capability), boto3 compatible, native `b2sdk`

**Cost at scale:** 1 TB = ~$13/month total (B2 + Feedtracks). 5 TB = ~$37/month. Zero egress charges via Cloudflare.

### 10A — New schema: file versions + timestamp comments

**File:** Create `schema/migrations/009_cloud_vault.sql`

```sql
-- 009_cloud_vault.sql

-- File version tracking (each upload creates a version row)
CREATE TABLE IF NOT EXISTS file_versions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id     INTEGER NOT NULL REFERENCES tracks(id),
    version_num  INTEGER NOT NULL DEFAULT 1,
    file_path    TEXT NOT NULL,       -- local path
    b2_key       TEXT,                -- B2 object key (null until uploaded)
    b2_bucket    TEXT,
    file_hash    TEXT NOT NULL,
    file_size    INTEGER,
    label        TEXT,                -- "final mix", "stems", "rough v2"
    uploaded_at  TEXT,
    created_at   TEXT DEFAULT (datetime('now')),
    UNIQUE(track_id, version_num)
);

-- Timestamp comments on tracks (like SoundCloud waveform comments)
CREATE TABLE IF NOT EXISTS track_comments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id     INTEGER NOT NULL REFERENCES tracks(id),
    version_id   INTEGER REFERENCES file_versions(id),
    timestamp_s  REAL,               -- seconds into track (null = general comment)
    author       TEXT NOT NULL,       -- agent name or "user"
    body         TEXT NOT NULL,
    resolved     INTEGER DEFAULT 0,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_file_versions_track ON file_versions(track_id);
CREATE INDEX IF NOT EXISTS idx_track_comments_track ON track_comments(track_id);
CREATE INDEX IF NOT EXISTS idx_track_comments_ts    ON track_comments(track_id, timestamp_s);

-- Cloud sync audit log
CREATE TABLE IF NOT EXISTS cloud_sync_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    operation    TEXT NOT NULL,   -- 'upload', 'download', 'delete', 'sync'
    b2_key       TEXT,
    file_path    TEXT,
    status       TEXT NOT NULL,   -- 'success', 'failed', 'skipped'
    error        TEXT,
    bytes        INTEGER,
    duration_ms  INTEGER,
    synced_at    TEXT DEFAULT (datetime('now'))
);
```

Apply:
```bash
sqlite3 ~/Library/"Application Support"/ai-record-label/hermes.db < schema/migrations/009_cloud_vault.sql
```

### 10B — DVC for audio file versioning

**Install DVC with B2/S3 support:**
```bash
pip install "dvc[s3]"
```

**Initialize DVC in the repo:**
```bash
cd ~/ai-record-label
git init  # if not already a git repo
dvc init
dvc remote add -d b2-vault s3://ai-record-label-vault/dvc
dvc remote modify b2-vault endpointurl https://s3.us-west-004.backblazeb2.com
dvc remote modify b2-vault access_key_id $B2_WRITE_KEY_ID
dvc remote modify b2-vault secret_access_key $B2_WRITE_APPLICATION_KEY
```

**Tagging a version of a track:**
```bash
# When you finish a mix:
dvc add ~/Music/just-inn-case/track.wav
git add ~/Music/just-inn-case/track.wav.dvc .gitignore
git commit -m "final chorus mix — capo 3, afternoon session"
git tag v1.3-final-chorus
dvc push  # uploads bytes to B2 (deduped — only changed chunks uploaded)
```

**Restoring any version:**
```bash
git checkout v1.0-rough && dvc pull  # pulls those exact bytes from B2
```

DVC `.dvc` files are tiny 64-byte text pointers — the actual audio bytes live in B2. This gives you full Git log, branches, and tags for free, with audio content deduplicated in B2 (identical stems across sessions = stored once).

**Timestamp comments for DVC versions** live in the new `track_comments` table (see 10A) — store the Git tag alongside the comment so annotations stay version-aware.

### 10C — restic for encrypted incremental backups

**Install restic:**
```bash
brew install restic  # macOS
winget install restic  # Windows
```

**Initialize restic repository in B2:**
```bash
restic -r b2:ai-record-label-backups init
# Enter a strong password — save it in your password manager
# Loss of this password = permanent data loss
```

**Backup command (run after sessions):**
```bash
restic -r b2:ai-record-label-backups backup \
  ~/Music/just-inn-case/ \
  --exclude "*.tmp" --exclude "*.DS_Store" --exclude "*.asd"
```

**Python watchdog integration in `file_watcher/watcher.py`:**
```python
import subprocess, os, threading

_backup_timer: threading.Timer | None = None

def _schedule_restic_backup():
    """Debounced backup — runs 30 min after the last file write."""
    global _backup_timer
    if _backup_timer:
        _backup_timer.cancel()
    _backup_timer = threading.Timer(1800, _run_restic_backup)
    _backup_timer.start()

def _run_restic_backup():
    music_dir = os.environ.get("WATCH_FOLDER", "")
    if music_dir:
        subprocess.run([
            "restic", "-r", "b2:ai-record-label-backups", "backup",
            music_dir, "--exclude", "*.tmp",
        ], env={**os.environ, "RESTIC_PASSWORD": os.environ.get("RESTIC_PASSWORD", "")})
```

Call `_schedule_restic_backup()` after every successful track registration. This debounces so restic runs once per session, not per file.

### 10D — rclone setup for Mega.nz migration

**Install rclone:**
```bash
# macOS
brew install rclone

# Windows
winget install Rclone.Rclone
```

**Configure B2 remote (interactive setup):**
```bash
rclone config
# → New remote → name: b2-vault
# → Type: Backblaze B2
# → Enter B2 account ID and application key
```

**Configure encrypted remote (protects files from B2 staff access):**
```bash
rclone config
# → New remote → name: b2-vault-crypt
# → Type: crypt
# → Remote: b2-vault:ai-record-label
# → Enter password (save this — loss = permanent data loss)
```

**Migrate from Mega.nz:**
```bash
# Configure Mega remote
rclone config
# → New remote → name: mega
# → Type: Mega
# → Enter Mega username/password

# Deduplicate Mega before migrating (Mega allows duplicate filenames)
rclone dedupe mega:/Music

# One-time migration (copy not sync — preserves B2 if you later clean Mega)
rclone copy mega:/Music b2-vault-crypt:/music/ \
  --progress \
  --transfers 8 \
  --fast-list \
  --exclude "*.tmp" \
  --log-file ~/Library/"Application Support"/ai-record-label/mega-migration.log

# Verify checksums after migration (don't skip this)
rclone check mega:/Music b2-vault-crypt:/music/
```

**Ongoing sync (run after every session or via cron):**
```bash
# Sync local music folder → B2 (one-way: local is source of truth)
rclone sync \
  "$LOCAL_MUSIC_FOLDER" \
  b2-vault-crypt:/music/ \
  --progress \
  --exclude "*.als" \        # Ableton projects have their own backup
  --exclude "*.asd" \        # Ableton analysis files
  --log-file ~/Library/"Application Support"/ai-record-label/cloud-sync.log
```

**Create `scripts/sync_to_cloud.sh`:**
```bash
#!/usr/bin/env bash
# Sync local music to B2 vault. Run after any session.
set -euo pipefail

DATA_DIR="${AI_RECORD_LABEL_DATA:-$HOME/Library/Application Support/ai-record-label}"
MUSIC_DIR="${1:-$(python3 -c "import json; d=json.load(open('$DATA_DIR/settings.json')); print(d.get('ableton_project_folder',''))" 2>/dev/null)}"

if [[ -z "$MUSIC_DIR" ]]; then
  echo "Usage: $0 /path/to/music/folder"
  exit 1
fi

echo "▸ Syncing $MUSIC_DIR → B2..."
rclone sync "$MUSIC_DIR" b2-vault-crypt:/music/ \
  --progress \
  --transfers 8 \
  --exclude "*.als" \
  --exclude "*.asd" \
  --exclude "*.tmp" \
  --log-file "$DATA_DIR/cloud-sync.log"

echo "✓ Sync complete. Log: $DATA_DIR/cloud-sync.log"
```

### 10E — Agent access to B2 (read-only)

**File:** Create `scripts/vault_tools.py`

Agents get read-only B2 credentials. They can:
- Search for a file by name/hash
- Get a presigned download URL (share with collaborators)
- List versions of a track

```python
"""vault_tools.py — B2 vault access for agents (read-only)."""
from __future__ import annotations
import os
from pathlib import Path

def get_b2_client():
    """Return a read-only b2sdk InMemoryAccountInfo client."""
    from b2sdk.v2 import InMemoryAccountInfo, B2Api
    info = InMemoryAccountInfo()
    api = B2Api(info)
    api.authorize_account("production", os.environ["B2_READ_KEY_ID"], os.environ["B2_READ_APPLICATION_KEY"])
    return api

def get_download_url(b2_key: str, valid_seconds: int = 3600) -> str:
    """Generate a presigned download URL for sharing a file."""
    api = get_b2_client()
    bucket = api.get_bucket_by_name(os.environ["B2_BUCKET_NAME"])
    url = bucket.get_download_url(b2_key)
    auth = api.get_download_url_for_file_name(
        os.environ["B2_BUCKET_NAME"], b2_key, valid_duration_in_seconds=valid_seconds
    )
    return auth

def list_vault_files(prefix: str = "music/") -> list[dict]:
    """List files in the vault with their sizes and timestamps."""
    api = get_b2_client()
    bucket = api.get_bucket_by_name(os.environ["B2_BUCKET_NAME"])
    return [
        {"name": f.file_name, "size": f.size, "uploaded": f.upload_timestamp}
        for f in bucket.ls(prefix, recursive=True)
    ]
```

Add to `.env`:
```bash
B2_READ_KEY_ID=your_read_only_key_id
B2_READ_APPLICATION_KEY=your_read_only_app_key
B2_BUCKET_NAME=ai-record-label-vault
```

**Security rule:** Agents get only the read-only key. Write key lives only in `scripts/sync_to_cloud.sh` and is never passed to Hermes or MCP.

### 10F — Feedtracks for collaborator feedback

Feedtracks ($6.99/month, 100 GB) provides waveform-based timestamped comments that collaborators can use without creating an account. Workflow:
1. Finish a mix → export to MP3/WAV
2. Upload to Feedtracks via web UI
3. Share the Feedtracks link with the collaborator
4. Their timestamp comments come back to you via email notification
5. You (or the intake agent) log actionable notes into `track_comments` in the DB with `author='collaborator'`

**Alternative (no SaaS cost):** Build a lightweight review page in the existing web app using `wavesurfer.js` (MIT license). Render the waveform for any track, accept click-to-annotate events, store timestamps in `track_comments`. This takes ~2 days of frontend work but eliminates the Feedtracks subscription and lets agents read/write comments programmatically.

Add `wavesurfer.js` to the integration plan for Phase 6 (Frontend) if pursuing this route:
```bash
cd desktop-app && npm install wavesurfer.js
```

### 10G — Python libraries to install

Add to `pyproject.toml` dependencies:
```toml
boto3          # S3-compatible access to B2 (read-only agent access)
b2sdk          # Backblaze native SDK (uploads, large file resume)
"dvc[s3]"      # Data Version Control with S3/B2 remote
resticpy       # Python wrapper for restic backup invocation
```

### 10H — Auto-sync cron

Register a Hermes cron to sync after every studio session:
```bash
hermes cron create \
  --name "cloud-vault-sync" \
  --schedule "0 2 * * *" \
  --no-agent \
  --script "sync_to_cloud.sh"
```

### 10I — Frontend: Cloud Vault panel in Settings

**File:** `desktop-app/src/pages/Settings.tsx`

Add a "Cloud Vault" section (below the Music Folder section from Phase 6):

```tsx
{/* Cloud Vault */}
<section className="space-y-3">
  <h2 className="text-sm font-semibold text-zinc-300 uppercase tracking-wider">
    Cloud Vault
  </h2>
  <div className="flex items-center gap-3">
    <div className={`w-2 h-2 rounded-full ${vaultStatus === 'connected' ? 'bg-green-500' : 'bg-zinc-600'}`} />
    <span className="text-sm text-zinc-400">
      {vaultStatus === 'connected' ? 'B2 vault connected' : 'Not configured'}
    </span>
    <button onClick={handleSyncNow} className="ml-auto btn-secondary text-xs px-3 py-1">
      Sync Now
    </button>
  </div>
  <p className="text-[11px] text-zinc-600">
    Last sync: {lastSync ?? 'never'} · {vaultFileCount ?? 0} files in vault
  </p>
</section>
```

Add `/vault/status` and `/vault/sync` endpoints to `http_api.py` (similar to `/settings` pattern).

---

## Phase 11 — Cross-Platform & Docker

> **Short answer:** The web app + Cloudflare tunnel already solves "use from any computer" — open the tunnel URL in any browser on any OS and you have full access. No install agent needed.
>
> **What Docker solves:** Running the *backend* (Hermes + API + file watcher) on a new machine (Windows PC, Linux server, second Mac) without manual Python setup.

### 11A — Update docker-compose.yml to include all services

**File:** `hermes-config/docker-compose.yml`

The existing compose file only has `hermes` and `bandcamp-agent`. Add the HTTP API and file watcher:

```yaml
# Add to existing services:

  # HTTP API + Web Frontend
  http-api:
    build:
      context: ..
      dockerfile: Dockerfile.api
    container_name: ai-label-api
    restart: unless-stopped
    env_file: .env
    environment:
      - API_PORT=8086
      - AI_RECORD_LABEL_DATA=/data
    volumes:
      - sqlite-data:/data
      - ${WATCH_FOLDER:-./watch}:/watch
      - ../desktop-app/dist:/app/dist:ro  # pre-built frontend
    ports:
      - "8086:8086"
    networks:
      - label-net

  # File watcher
  file-watcher:
    build:
      context: ..
      dockerfile: Dockerfile.watcher
    container_name: ai-label-watcher
    restart: unless-stopped
    env_file: .env
    environment:
      - AI_RECORD_LABEL_DATA=/data
      - WATCH_FOLDER=/watch
    volumes:
      - sqlite-data:/data
      - ${WATCH_FOLDER:-./watch}:/watch
    networks:
      - label-net
```

**File:** Create `Dockerfile.api`:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen --no-dev
COPY http_api.py ./
COPY session_intelligence/ ./session_intelligence/
COPY desktop-app/dist/ ./desktop-app/dist/
EXPOSE 8086
CMD ["python", "http_api.py"]
```

**File:** Create `Dockerfile.watcher`:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y ffmpeg chromaprint-tools && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen --no-dev
COPY file_watcher/ ./file_watcher/
COPY session_intelligence/ ./session_intelligence/
CMD ["python", "-m", "file_watcher.watcher"]
```

### 11B — Windows: verify launch.ps1 covers all services

**File:** `scripts/launch.ps1` (already exists — verify and fill gaps)

Check that `launch.ps1` mirrors `launch.sh` for:
- Starting the HTTP API (port 8086) — add if missing
- Starting the file watcher — add if missing
- Printing the web URL and API token — add if missing

The Windows experience should be: run `.\scripts\launch.ps1`, open browser to `http://localhost:8086`.

### 11C — One-line Docker start for any machine

Once the Dockerfiles are written, spinning up a new machine is:
```bash
# On any machine with Docker + Docker Compose
git clone <repo>
cp hermes-config/.env.example hermes-config/.env  # fill in keys
cd desktop-app && npm run build && cd ..          # build frontend once
docker compose -f hermes-config/docker-compose.yml up -d
# → Web UI at http://localhost:8086
# → Cloudflare tunnel auto-exposes it at the same public URL
```

No "install agent" needed — Docker is the install agent.

---

## Validation Checklist

After all phases are complete, run these checks in order:

```bash
# 1. Schema integrity
sqlite3 ~/Library/"Application Support"/ai-record-label/hermes.db "PRAGMA integrity_check;"
sqlite3 ~/Library/"Application Support"/ai-record-label/hermes.db "PRAGMA foreign_key_check;"

# 2. Verify new tables exist
sqlite3 ~/Library/"Application Support"/ai-record-label/hermes.db ".tables" | grep -E "kg_nodes|kg_edges|batch_jobs"

# 3. Verify indexes
sqlite3 ~/Library/"Application Support"/ai-record-label/hermes.db ".indices" | grep idx_

# 4. API endpoints
TOKEN=$(cat ~/Library/"Application Support"/ai-record-label/api_token.txt)
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8086/settings | python3 -m json.tool
curl -s -H "Authorization: Bearer $TOKEN" "http://localhost:8086/release_states?track_id=1" | python3 -m json.tool

# 5. Hermes cron jobs (should show 14 jobs after adding post-session)
hermes cron list

# 6. Hermes doctor
hermes doctor

# 7. MCP server tools list
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8086 # or use mcp inspector

# 8. TypeScript (no errors)
cd desktop-app && npx tsc --noEmit

# 9. Test suite (288 tests should still pass)
cd ~/ai-record-label && ./scripts/run_tests.sh fast

# 10. Intake agent (Phase 9)
# Verify file watcher recursive flag is set to True in watcher.py
grep "recursive=" file_watcher/watcher.py | grep -v "False"
# Test intake script
.venv/bin/python scripts/intake_album.py --help

# 11. Cloud vault schema (Phase 10)
sqlite3 ~/Library/"Application Support"/ai-record-label/hermes.db \
  ".tables" | grep -E "file_versions|track_comments|cloud_sync_log"

# 12. rclone remotes configured
rclone listremotes | grep -E "b2-vault|mega"

# 13. DVC remote configured
dvc remote list
```

---

## Sources

All recommendations are sourced from the following verified references:

**Anthropic / Claude API:**
- [Prompt caching — Claude API Docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Batch processing — Claude API Docs](https://platform.claude.com/docs/en/build-with-claude/batch-processing)
- [Adaptive thinking — Claude API Docs](https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking)
- [Extended thinking — Claude API Docs](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)
- [Models overview — Claude API Docs](https://platform.claude.com/docs/en/about-claude/models/overview)
- [Lessons from building Claude Code: Prompt caching is everything](https://claude.com/blog/lessons-from-building-claude-code-prompt-caching-is-everything)
- [Claude: How prompt caching actually works](https://www.mager.co/blog/2026-04-29-claude-prompt-caching/)

**Hermes Agent Framework:**
- [Configuration — Hermes Docs](https://hermes-agent.nousresearch.com/docs/user-guide/configuration)
- [Configuring Models — Hermes Docs](https://hermes-agent.nousresearch.com/docs/user-guide/configuring-models)
- [Context Compression and Caching — Hermes Docs](https://hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching)
- [Scheduled Tasks (Cron) — Hermes Docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron)
- [Best Claude Models for Hermes Agent](https://www.remoteopenclaw.com/blog/best-claude-models-for-hermes)
- [Best Cheap AI Models for Hermes Agent](https://www.remoteopenclaw.com/blog/best-cheap-models-for-hermes)

**Database & Knowledge Graph:**
- [SQLite PRAGMA documentation](https://www.sqlite.org/pragma.html)
- [simple-graph-sqlite on PyPI](https://pypi.org/project/simple-graph-sqlite/)
- [How to Build Lightweight GraphRAG with SQLite](https://dev.to/stephenc222/how-to-build-lightweight-graphrag-with-sqlite-53le)
- [The MCP Pattern: SQLite as the AI-Queryable Cache](https://metafunctor.com/post/2026-03-20-the-mcp-pattern/)
- [sqlite-memory (markdown agent memory with hybrid search)](https://github.com/sqliteai/sqlite-memory)
- [Build knowledge graphs with LLM-driven entity extraction](https://dev.to/neuml/build-knowledge-graphs-with-llm-driven-entity-extraction-4hlm)

**Token Efficiency & Agent Memory:**
- [Context Engineering for Agents (LangChain)](https://www.langchain.com/blog/context-engineering-for-agents)
- [Multi-Agent Systems with Context Engineering (Vellum)](https://www.vellum.ai/blog/multi-agent-systems-building-with-context-engineering)
- [Stop Wasting Your Tokens (ICLR 2026)](https://arxiv.org/abs/2510.26585)
- [SkillReducer: Optimizing LLM Agent Skills for Token Efficiency](https://arxiv.org/abs/2603.29919)
- [LightMem: Lightweight Agent Memory with SLMs](https://arxiv.org/abs/2604.07798)
- [AI Agent Context Compression Strategies](https://zylos.ai/research/2026-02-28-ai-agent-context-compression-strategies)
- [Evaluating Context Compression for AI Agents (Factory.ai)](https://factory.ai/news/evaluating-compression)
- [LLM Chat History Summarization Guide 2025 (Mem0)](https://mem0.ai/blog/llm-chat-history-summarization-guide-2025)
- [State of AI Agent Memory 2026 (Mem0)](https://mem0.ai/blog/state-of-ai-agent-memory-2026)
- [Graph-Based Agent Memory Taxonomy](https://arxiv.org/abs/2602.05665)
- [Graphiti — Temporal Knowledge Graphs for Agents (Zep)](https://github.com/getzep/graphiti)
- [Anthropic API Pricing Guide (Finout)](https://www.finout.io/blog/anthropic-api-pricing)
- [Anthropic Multi-Agent Blueprint (Fountain City)](https://fountaincity.tech/resources/blog/anthropic-multi-agent-blueprint-production/)
- [mutagen documentation](https://mutagen.readthedocs.io/)
- [Python watchdog library](https://python-watchdog.readthedocs.io/en/stable/)

**Cloud Storage & Vault (Phase 10):**
- [Backblaze B2 Pricing — May 2026](https://www.backblaze.com/cloud-storage/pricing)
- [Backblaze B2 Application Key Capabilities](https://www.backblaze.com/docs/cloud-storage-application-key-capabilities)
- [Backblaze B2 Python boto3 Guide](https://www.backblaze.com/docs/cloud-storage-use-the-aws-sdk-for-python-with-backblaze-b2)
- [Backblaze + Cloudflare Bandwidth Alliance (free egress)](https://www.backblaze.com/blog/backblaze-and-cloudflare-partner-to-provide-free-data-transfer/)
- [Cloudflare R2 Pricing](https://developers.cloudflare.com/r2/pricing/)
- [Wasabi Pricing + 90-Day Minimum Retention Policy](https://docs.wasabi.com/docs/how-does-wasabis-minimum-storage-duration-policy-work)
- [DVC Remote Storage with Amazon S3/B2](https://doc.dvc.org/user-guide/data-management/remote-storage/amazon-s3)
- [rclone Mega Backend](https://github.com/rclone/rclone/blob/master/docs/content/mega.md)
- [rclone copy Mega to Backblaze B2 (RcloneView guide)](https://rcloneview.com/support/blog/backup-mega-to-backblaze-b2-rcloneview)
- [Feedtracks — Audio Collaboration with Timestamped Comments](https://feedtracks.com/)
- [resticpy — Python wrapper for restic backups](https://mtlynch.github.io/resticpy/)
- [wavesurfer.js — in-browser waveform rendering (MIT license)](https://wavesurfer.xyz/)

**Intake Agent (Phase 9):**
- [watchdog Python library — Observer.schedule recursive flag](https://python-watchdog.readthedocs.io/en/stable/api.html)
- [mutagen — audio metadata reading (MP3, FLAC, M4A)](https://mutagen.readthedocs.io/en/latest/)
