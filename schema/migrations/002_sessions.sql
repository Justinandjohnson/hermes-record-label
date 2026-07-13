-- =============================================================================
-- AI Record Label — Session Intelligence Migration (002)
-- =============================================================================
-- Adds tables for tracking Ableton Live sessions, project versions, and
-- export events with audio fingerprinting and change detection.
--
-- Run via: sqlite3 <db_path> < 002_sessions.sql
-- =============================================================================

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- -----------------------------------------------------------------------------
-- Ableton sessions (one row per inferred work session, grouped from backups)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ableton_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL,
    project_path TEXT,
    session_date TEXT NOT NULL,         -- 'YYYY-MM-DD' (local date of started_at)
    started_at TIMESTAMP NOT NULL,
    ended_at TIMESTAMP,
    duration_minutes REAL,
    save_count INTEGER DEFAULT 0,
    export_count INTEGER DEFAULT 0,
    bpm REAL,
    time_sig_num INTEGER,
    time_sig_den INTEGER,
    musical_key TEXT,
    track_count INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (project_name, started_at)
);

CREATE INDEX IF NOT EXISTS idx_ableton_sessions_session_date
    ON ableton_sessions (session_date);
CREATE INDEX IF NOT EXISTS idx_ableton_sessions_project_name
    ON ableton_sessions (project_name);

-- -----------------------------------------------------------------------------
-- Project versions (one row per .als backup snapshot)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES ableton_sessions(id) ON DELETE CASCADE,
    project_name TEXT NOT NULL,
    als_path TEXT NOT NULL,
    als_hash TEXT,
    saved_at TIMESTAMP NOT NULL,
    bpm REAL,
    time_sig_num INTEGER,
    time_sig_den INTEGER,
    musical_key TEXT,
    track_names TEXT,                   -- JSON array
    plugin_names TEXT,                  -- JSON array
    diff_from_prev TEXT,                -- JSON object
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (als_path)
);

CREATE INDEX IF NOT EXISTS idx_project_versions_project_name
    ON project_versions (project_name);
CREATE INDEX IF NOT EXISTS idx_project_versions_session_id
    ON project_versions (session_id);

-- -----------------------------------------------------------------------------
-- Export events (audio renders/bounces from Ableton)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS export_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES ableton_sessions(id) ON DELETE SET NULL,
    project_name TEXT,
    file_path TEXT NOT NULL UNIQUE,
    file_hash TEXT,
    fingerprint TEXT,
    changed_from_prev INTEGER,          -- 0 / 1 boolean
    similarity_score REAL,
    file_size INTEGER,
    duration_seconds REAL,
    sample_rate INTEGER,
    channels INTEGER,
    detected_bpm REAL,
    exported_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_export_events_project_name
    ON export_events (project_name);
CREATE INDEX IF NOT EXISTS idx_export_events_session_id
    ON export_events (session_id);
CREATE INDEX IF NOT EXISTS idx_export_events_exported_at
    ON export_events (exported_at);
