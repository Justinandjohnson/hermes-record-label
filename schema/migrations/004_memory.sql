-- ============================================================================
-- 004_memory.sql — Agent memory and pattern tracking
--
-- Lightweight memory layer built on SQLite FTS5. No ChromaDB, no ONNX,
-- no external vector database. Agents store observations here via the
-- add_memory MCP tool and retrieve via search_memory (FTS5 keyword) or
-- get_agent_memory (structured, by agent/tag).
--
-- Design principles:
--   - Verbatim storage (no summarization — agents decide what matters)
--   - FTS5 full-text search for semantic-ish retrieval without embeddings
--   - Tagged observations so agents can filter by topic area
--   - Confidence tracking so patterns can strengthen over time
--   - Temporal: observations have created_at and a recency weight
-- ============================================================================

-- ── Core memory table ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_memory (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    agent        TEXT    NOT NULL,          -- nico | diane | mika | rex | studio
    tag          TEXT    NOT NULL,          -- observation category (see below)
    content      TEXT    NOT NULL,          -- verbatim observation, in agent's voice
    confidence   REAL    DEFAULT 0.5,       -- 0.0–1.0; increment on confirmation
    track_id     INTEGER REFERENCES tracks(id) ON DELETE SET NULL,
    created_at   TEXT    DEFAULT (datetime('now')),
    updated_at   TEXT    DEFAULT (datetime('now')),
    confirmed_at TEXT,                      -- last time this pattern was confirmed

    -- Soft-delete rather than hard-delete; preserves history
    archived     INTEGER DEFAULT 0
);

-- Tags / categories (enforced by convention, not FK — agents can add new ones)
--
-- nico:
--   arrangement_habit    — recurring structural choices (weak bridges, etc.)
--   genre_movement       — going deeper into an influence or broader across palette
--   emotional_range      — what feelings the artist explores vs. avoids
--   revision_pattern     — how many versions, over vs. under-revise
--   feedback_receptivity — what lands, what gets pushed back
--   confidence_arc       — risk-taking trajectory over time
--
-- diane:
--   deadline_behavior    — hits/misses, buffer needed, pressure vs. space
--   comm_cadence         — how often they check in
--   motivation_trigger   — what gets them moving
--   stall_pattern        — what causes silence, what resolved it
--   project_preference   — singles vs. EP vs. album, speed preference
--
-- mika:
--   visual_preference    — colors, textures, moods the artist gravitates toward
--   reference_language   — film/music/nature/architecture vocabulary they use
--   feedback_reception   — precise implementation vs. loose interpretation
--   visual_arc           — aesthetic sensibility developing over time
--   creative_block       — what causes visual stalls
--   palette              — specific hex values, typography choices, recurring motifs
--
-- rex:
--   file_hygiene         — export habits improving or recurring mistakes
--   common_preflight_err — which checks fail repeatedly
--   description_quality  — release page copy getting better over time
--   platform_engagement  — Bandcamp follower growth, buy vs. free-download ratio

CREATE INDEX IF NOT EXISTS idx_agent_memory_agent_tag
    ON agent_memory (agent, tag, archived);

CREATE INDEX IF NOT EXISTS idx_agent_memory_track
    ON agent_memory (track_id)
    WHERE track_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_agent_memory_confidence
    ON agent_memory (agent, confidence DESC)
    WHERE archived = 0;

-- ── FTS5 virtual table for keyword search ────────────────────────────────
-- Allows agents to search memory by natural language query.
-- FTS5 porter tokenizer handles stemming (listen/listening, arrange/arrangement).
CREATE VIRTUAL TABLE IF NOT EXISTS agent_memory_fts USING fts5(
    content,
    agent    UNINDEXED,
    tag      UNINDEXED,
    content='agent_memory',
    content_rowid='id',
    tokenize='porter unicode61'
);

-- Keep FTS5 in sync via triggers
CREATE TRIGGER IF NOT EXISTS agent_memory_ai
    AFTER INSERT ON agent_memory BEGIN
        INSERT INTO agent_memory_fts(rowid, content, agent, tag)
        VALUES (new.id, new.content, new.agent, new.tag);
    END;

CREATE TRIGGER IF NOT EXISTS agent_memory_ad
    AFTER DELETE ON agent_memory BEGIN
        INSERT INTO agent_memory_fts(agent_memory_fts, rowid, content, agent, tag)
        VALUES ('delete', old.id, old.content, old.agent, old.tag);
    END;

CREATE TRIGGER IF NOT EXISTS agent_memory_au
    AFTER UPDATE ON agent_memory BEGIN
        INSERT INTO agent_memory_fts(agent_memory_fts, rowid, content, agent, tag)
        VALUES ('delete', old.id, old.content, old.agent, old.tag);
        INSERT INTO agent_memory_fts(rowid, content, agent, tag)
        VALUES (new.id, new.content, new.agent, new.tag);
    END;

-- ── Pattern strength view ────────────────────────────────────────────────
-- Aggregates confirmed observations into "established patterns" per agent.
-- Confidence >= 0.7 after 2+ observations = established pattern.
CREATE VIEW IF NOT EXISTS established_patterns AS
SELECT
    agent,
    tag,
    COUNT(*)           AS observation_count,
    AVG(confidence)    AS avg_confidence,
    MAX(updated_at)    AS last_seen,
    GROUP_CONCAT(content, ' | ') AS pattern_summary
FROM agent_memory
WHERE archived = 0
  AND confidence >= 0.6
GROUP BY agent, tag
HAVING COUNT(*) >= 2
ORDER BY agent, avg_confidence DESC;

-- ── Artist context KV store ──────────────────────────────────────────────
-- A running key-value summary all agents can read. Updated by studio conductor
-- after significant interactions. Complements the structured artist_profile
-- table from 001_initial.sql with free-form agent observations.
CREATE TABLE IF NOT EXISTS artist_context (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ctx_key     TEXT    NOT NULL UNIQUE,   -- e.g. 'sound_palette', 'visual_world'
    ctx_value   TEXT    NOT NULL,
    updated_by  TEXT    NOT NULL,          -- which agent last updated
    updated_at  TEXT    DEFAULT (datetime('now'))
);

-- Seed initial context keys
INSERT OR IGNORE INTO artist_context (ctx_key, ctx_value, updated_by) VALUES
    ('sound_palette',     'Indie, alt, hip-hop, trap, neo-soul, soul, jazz — genre-blending, emotionally exploratory', 'studio'),
    ('creative_theme',    'Exploring musical ideas and the emotions they unlock', 'studio'),
    ('team_vibe',         'Thoughtful and measured', 'studio'),
    ('releases_count',    '0', 'studio'),
    ('creation_streak',   '0', 'studio');
