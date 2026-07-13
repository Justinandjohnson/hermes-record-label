# AI Record Label — Agent Skills Reference

> This document is the authoritative reference for agent capabilities. Agents should consult this
> before writing custom logic for any operation already covered here. All functions listed are
> production-ready with proper error handling.

---

## Intake & Catalog

### Intake an album from the CLI (immediate, no agent needed)
```bash
# Import an album folder (copies to inbox, registers in DB)
AI_RECORD_LABEL_DATA=~/Library/"Application Support"/ai-record-label \
  .venv/bin/python scripts/intake_album.py "/path/to/Album Folder"

# Options
--title "Override Title"  # override ID3 album tag
--year 2023               # override release year
--type ep                 # single | ep | album (default: album)
--state DRAFT             # DRAFT | IN_REVIEW | APPROVED (default: DRAFT)
--no-copy                 # register files in place, don't copy to inbox
```

### Trigger intake from a Hermes agent
```
send_message to: intake
body: "New drop in /path/to/folder — please intake"
```

### Check for duplicates before registering
```python
from file_watcher.track_registry import sha256_of
# The intake pipeline checks file_hash automatically.
# Manual check: SELECT id FROM tracks WHERE file_hash = '<hash>'
```

---

## Cloud Vault Operations

### Search vault for a file
```
tool: vault.search_vault
params: { query: "album name or track title" }
```

### Get a share URL for a track
```
tool: vault.get_share_url_for_track
params: { track_id: 42, valid_hours: 48 }
→ returns { url: "https://...", expires_in_hours: 48 }
```

### Add a timestamped comment to a track
```
tool: vault.add_track_comment
params: {
  track_id: 42,
  body: "Snare is too loud from 0:45 to 1:10",
  author: "a_and_r",
  timestamp_s: 45.0
}
```

### Check when the vault was last synced
```
tool: vault.get_sync_status
→ returns { last_successful_sync: "2026-05-15T02:00:00Z", ... }
```

### Manually trigger a cloud sync (shell)
```bash
./scripts/sync_to_cloud.sh                    # uses folder from settings.json
./scripts/sync_to_cloud.sh /path/to/music     # explicit folder
./scripts/sync_to_cloud.sh --status           # show last sync log
```

---

## Knowledge Graph

### Add a track to the KG
```
tool: kg_add_node
params: { id: "track:42", type: "track", label: "Song Title" }

tool: kg_add_edge
params: { source: "album:soundscapes-vol-2", target: "track:42", relation: "contains" }
```

### Search the KG by keyword
```
tool: kg_search
params: { query: "lo-fi ambient" }
→ returns nodes matching the FTS5 full-text search
```

### Common KG node types
| type | id format | example |
|------|-----------|---------|
| track | `track:{db_id}` | `track:42` |
| album | `album:{slug}` | `album:soundscapes-vol-2` |
| genre | `genre:{name}` | `genre:lo-fi` |
| mood | `mood:{name}` | `mood:melancholic` |
| instrument | `instrument:{name}` | `instrument:piano` |
| plugin | `plugin:{name}` | `plugin:serum` |

---

## Audio Analysis

### Analyze a track (Gemini)
```
tool: audio_analysis.analyze_track
params: { file_path: "/absolute/path/to/track.mp3", track_id: 42 }
```

**Always check the cache first:**
```sql
SELECT id FROM audio_analyses WHERE track_id = 42
```
If a row exists, don't call analyze_track again — the analysis is already done.

### Get artist evolution arc
```
tool: audio_analysis.get_evolution_arc
→ returns chronological list of sound evolution notes
```

---

## File System

### Browse the music folder
```
tool: browse_folder
params: { path: "/path/to/music/folder", pattern: "*.mp3" }
```

### Read audio metadata without analyzing
```
tool: read_audio_metadata
params: { file_path: "/path/to/track.mp3" }
→ returns { title, artist, album, bpm, duration_seconds, tracknumber }
```

---

## Messaging & Notifications

### Send a message between agents
```
tool: send_message
params: { to: "a_and_r", body: "New intake: 12 tracks in project_id=47" }
```

### Send SMS notification
```
tool: send_sms
params: {
  body: "New album drop: Soundscapes Vol 2 (12 tracks). Check the A&R queue.",
  channel: "sms"
}
```
SMS is for significant events only: album completions, releases going live, session summaries.
Don't SMS for routine A&R reviews or individual track updates.

---

## Database Quick Reference

All agents have read access to these tables. Key fields:

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `tracks` | Every audio file | id, title, file_path, file_hash, state, project_id |
| `projects` | Albums/EPs/singles | id, title, type, state, target_track_count |
| `audio_analyses` | Gemini analysis results | track_id, genre_tags, mood_tags, bpm, instruments |
| `track_comments` | Timestamped annotations | track_id, timestamp_s, author, body, resolved |
| `file_versions` | Cloud vault versions | track_id, b2_key, version_num, label |
| `kg_nodes` | Knowledge graph entities | id, type, label, properties |
| `kg_edges` | Knowledge graph relations | source, target, relation, weight |
| `feedback` | Agent feedback on tracks | track_id, agent, rating, notes |
| `release_states` | Release pipeline state | track_id, state, updated_at |
| `agent_memory` | Long-term agent memory | agent, category, observation, confidence |

### Common queries
```sql
-- All DRAFT tracks not yet analyzed
SELECT t.id, t.title FROM tracks t
LEFT JOIN audio_analyses aa ON aa.track_id = t.id
WHERE t.state = 'DRAFT' AND aa.id IS NULL;

-- Tracks in a project
SELECT id, title, state, duration_seconds FROM tracks WHERE project_id = 47;

-- Unresolved comments on a track
SELECT timestamp_s, author, body FROM track_comments
WHERE track_id = 42 AND resolved = 0
ORDER BY timestamp_s;

-- KG: all tracks with genre 'lo-fi'
SELECT source FROM kg_edges
WHERE target = 'genre:lo-fi' AND relation = 'has_genre';
```

---

## Token Efficiency Rules (for all agents)

1. **Check DB before calling any analysis tool** — don't re-analyze what's already in `audio_analyses`
2. **Use KG search first** for catalog questions — don't load all track rows
3. **Set `reasoning_effort: low`** for routine intake/logging tasks
4. **Set `reasoning_effort: high`** only for creative decisions (A&R judgments, release strategy)
5. **Never load file contents** for metadata — use `read_audio_metadata` instead of reading binary
6. **Cache the artist profile** at session start — don't re-query for every track decision

---

## Cross-Platform Notes

- **Using the system from another computer**: open the Cloudflare tunnel URL in any browser. Done.
- **Running the backend on a new Mac**: `./scripts/launch.sh`
- **Running on Windows**: `.\scripts\launch.ps1`
- **Running in Docker**: `docker compose -f hermes-config/docker-compose.yml up -d`
- **The Windows path bug** (Phase 5): remote Windows paths like `C:\Users\...` will fail `is_file()` on macOS. The fix is in INTEGRATION_PLAN.md Phase 5 — don't merge remote event file paths with local filesystem checks.
