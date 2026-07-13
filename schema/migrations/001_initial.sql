-- =============================================================================
-- AI Record Label — Initial Schema Migration
-- =============================================================================
-- SQLite database schema for the AI Record Label system.
-- Run via: sqlite3 <db_path> < 001_initial.sql
--
-- Requires WAL mode for concurrent read access (Hermes + Desktop app).
-- =============================================================================

-- Enable WAL mode for concurrent readers (Hermes + Tauri desktop app)
PRAGMA journal_mode=WAL;

-- Enable foreign key enforcement (off by default in SQLite)
PRAGMA foreign_keys=ON;

-- =============================================================================
-- Core Tables
-- =============================================================================

-- Artist profile and preferences (single-tenant: one row)
CREATE TABLE IF NOT EXISTS artist_profile (
    id INTEGER PRIMARY KEY DEFAULT 1,
    name TEXT NOT NULL,
    genre TEXT,
    subgenres TEXT,            -- JSON array: ["lo-fi", "ambient"]
    influences TEXT,           -- JSON array: ["Artist A", "Artist B"]
    sound_description TEXT,
    bandcamp_url TEXT,
    quiet_hours_start TEXT,    -- "22:00" (HH:MM format)
    quiet_hours_end TEXT,      -- "09:00" (HH:MM format)
    quiet_days TEXT,           -- JSON array: ["saturday", "sunday"]
    timezone TEXT DEFAULT 'America/Los_Angeles',
    onboarded_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tracks with versioning support
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,       -- SHA-256 for deduplication
    file_size INTEGER,
    duration_seconds REAL,
    format TEXT,                    -- wav, mp3, flac, aiff, ogg
    parent_track_id INTEGER REFERENCES tracks(id),  -- links revisions to original
    version INTEGER DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'DRAFT',
    project_id INTEGER REFERENCES projects(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Projects (singles, EPs, albums — the "Deal")
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    type TEXT NOT NULL,             -- 'single', 'ep', 'album'
    state TEXT NOT NULL DEFAULT 'active',
    target_track_count INTEGER,
    target_release_date TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- Audio Analysis & Memory
-- =============================================================================

-- Audio analysis results from Gemini 3.1 Pro
CREATE TABLE IF NOT EXISTS audio_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER NOT NULL REFERENCES tracks(id),
    model_used TEXT NOT NULL DEFAULT 'gemini-3.1-pro',
    bpm REAL,
    musical_key TEXT,
    energy_curve TEXT,             -- JSON array: [{timestamp, energy_level}]
    structure TEXT,                -- JSON: {intro: "0:00-0:15", verse1: "0:15-0:45", ...}
    instruments TEXT,              -- JSON array: ["808", "synth pad", "guitar"]
    genre_tags TEXT,               -- JSON array: ["hip-hop", "lo-fi"]
    mood_tags TEXT,                -- JSON array: ["melancholic", "introspective"]
    mix_observations TEXT,         -- JSON array: [{timestamp, observation}]
    notable_moments TEXT,          -- JSON array: [{timestamp, description, quality_judgment}]
    raw_response TEXT,             -- full Gemini response for debugging
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Audio memory: cumulative pattern detection across all tracks
-- This is the "ears" memory — what the system learns about the artist's sound
CREATE TABLE IF NOT EXISTS audio_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    -- Valid categories:
    --   'signature_sound'      — defining characteristics of the artist's sound
    --   'recurring_strength'   — things the artist consistently does well
    --   'recurring_weakness'   — areas that consistently need work
    --   'genre_tendency'       — genre patterns across tracks
    --   'production_pattern'   — recurring production choices
    --   'arrangement_habit'    — structural patterns in arrangements
    --   'energy_preference'    — typical energy curves and dynamics
    --   'instrument_palette'   — frequently used instruments/sounds
    --   'evolution_note'       — how the sound is changing over time
    observation TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,       -- 0.0-1.0, grows with more data points
    first_noticed_track_id INTEGER REFERENCES tracks(id),
    supporting_track_ids TEXT,          -- JSON array of track IDs that confirm this
    times_observed INTEGER DEFAULT 1,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- Communication & Feedback
-- =============================================================================

-- Agent feedback log (all agent <-> artist messages)
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER REFERENCES tracks(id),
    project_id INTEGER REFERENCES projects(id),
    agent TEXT NOT NULL,            -- 'a_and_r', 'manager', 'creative_director', 'bandcamp'
    message TEXT NOT NULL,
    channel TEXT NOT NULL,          -- 'sms', 'desktop', 'voice'
    direction TEXT NOT NULL,        -- 'outbound' (agent->artist) or 'inbound' (artist->agent)
    intent TEXT,                    -- parsed: 'approval', 'rejection', 'revision', 'feedback',
                                   --         'question', 'nag', 'delay', 'casual'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Scheduled messages (timing engine queue)
CREATE TABLE IF NOT EXISTS scheduled_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    channel TEXT NOT NULL,
    message TEXT NOT NULL,
    scheduled_for TIMESTAMP NOT NULL,
    sent_at TIMESTAMP,
    context TEXT                    -- JSON: why this message, what triggered it
);

-- =============================================================================
-- Release Pipeline
-- =============================================================================

-- Release state machine audit log (every state transition is recorded)
CREATE TABLE IF NOT EXISTS release_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER NOT NULL REFERENCES tracks(id),
    from_state TEXT,
    to_state TEXT NOT NULL,
    changed_by TEXT NOT NULL,      -- agent name or 'artist' or 'system'
    reason TEXT,
    bandcamp_job_id TEXT,          -- links to Bandcamp agent job queue
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Artwork submissions and review history
CREATE TABLE IF NOT EXISTS artwork (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER REFERENCES tracks(id),
    project_id INTEGER REFERENCES projects(id),
    file_path TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'submitted',  -- submitted, approved, rejected
    review_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Bandcamp integration tracking
-- The Bandcamp agent manages its own internal state; this table tracks
-- the handoff between our coordination engine and the Bandcamp agent.
CREATE TABLE IF NOT EXISTS bandcamp_releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER NOT NULL REFERENCES tracks(id),
    album_path TEXT NOT NULL,               -- path to album dir on disk
    bandcamp_job_id TEXT,                   -- from POST /jobs/upload response
    bandcamp_album_id TEXT,                 -- from .bandcamp_info.json after upload
    preflight_result TEXT,                  -- JSON: result of POST /library/preflight-check
    upload_status TEXT DEFAULT 'pending',   -- pending, preflight_passed, uploading, uploaded, failed
    publish_approved_at TIMESTAMP,          -- when POST /library/review/decision was called
    uploaded_at TIMESTAMP,
    bandcamp_url TEXT,                      -- final Bandcamp URL after publish
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- Gamification
-- =============================================================================

-- Deal board milestones (gamified project progress)
CREATE TABLE IF NOT EXISTS milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,            -- 'Demo Review', 'Mix Approval', 'Master Delivery',
                                   -- 'Artwork', 'Release'
    gate_agent TEXT NOT NULL,      -- which agent must approve this milestone
    state TEXT NOT NULL DEFAULT 'pending',  -- pending, active, cleared, skipped
    cleared_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Creation streaks and aggregate stats
CREATE TABLE IF NOT EXISTS artist_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_type TEXT NOT NULL,       -- 'streak', 'reputation', 'weekly_summary'
    value TEXT NOT NULL,           -- JSON payload
    period_start TEXT,             -- for time-bound stats (ISO date)
    period_end TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- Indexes for Common Queries
-- =============================================================================

-- Track lookups
CREATE INDEX IF NOT EXISTS idx_tracks_state ON tracks(state);
CREATE INDEX IF NOT EXISTS idx_tracks_file_hash ON tracks(file_hash);
CREATE INDEX IF NOT EXISTS idx_tracks_parent ON tracks(parent_track_id);
CREATE INDEX IF NOT EXISTS idx_tracks_project ON tracks(project_id);
CREATE INDEX IF NOT EXISTS idx_tracks_created ON tracks(created_at);

-- Audio analysis lookups
CREATE INDEX IF NOT EXISTS idx_audio_analyses_track ON audio_analyses(track_id);
CREATE INDEX IF NOT EXISTS idx_audio_analyses_created ON audio_analyses(created_at);

-- Audio memory queries (A&R pattern retrieval)
CREATE INDEX IF NOT EXISTS idx_audio_memory_category ON audio_memory(category);
CREATE INDEX IF NOT EXISTS idx_audio_memory_confidence ON audio_memory(confidence DESC);
CREATE INDEX IF NOT EXISTS idx_audio_memory_first_track ON audio_memory(first_noticed_track_id);

-- Feedback queries (conversation history)
CREATE INDEX IF NOT EXISTS idx_feedback_track ON feedback(track_id);
CREATE INDEX IF NOT EXISTS idx_feedback_agent ON feedback(agent);
CREATE INDEX IF NOT EXISTS idx_feedback_direction ON feedback(direction);
CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at);

-- Release state audit trail
CREATE INDEX IF NOT EXISTS idx_release_states_track ON release_states(track_id);
CREATE INDEX IF NOT EXISTS idx_release_states_to ON release_states(to_state);
CREATE INDEX IF NOT EXISTS idx_release_states_created ON release_states(created_at);

-- Scheduled messages (timing engine queue)
CREATE INDEX IF NOT EXISTS idx_scheduled_messages_pending ON scheduled_messages(scheduled_for)
    WHERE sent_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_scheduled_messages_agent ON scheduled_messages(agent);

-- Artwork lookups
CREATE INDEX IF NOT EXISTS idx_artwork_track ON artwork(track_id);
CREATE INDEX IF NOT EXISTS idx_artwork_state ON artwork(state);

-- Bandcamp release tracking
CREATE INDEX IF NOT EXISTS idx_bandcamp_releases_track ON bandcamp_releases(track_id);
CREATE INDEX IF NOT EXISTS idx_bandcamp_releases_status ON bandcamp_releases(upload_status);
CREATE INDEX IF NOT EXISTS idx_bandcamp_releases_job ON bandcamp_releases(bandcamp_job_id);

-- Milestones (deal board)
CREATE INDEX IF NOT EXISTS idx_milestones_project ON milestones(project_id);
CREATE INDEX IF NOT EXISTS idx_milestones_state ON milestones(state);

-- Stats lookups
CREATE INDEX IF NOT EXISTS idx_artist_stats_type ON artist_stats(stat_type);
CREATE INDEX IF NOT EXISTS idx_artist_stats_period ON artist_stats(period_start, period_end);
