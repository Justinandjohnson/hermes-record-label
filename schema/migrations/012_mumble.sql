-- =============================================================================
-- Migration 012: Mumble Analysis
-- =============================================================================
-- Stores phonetic mumble decoding results for early-stage vocal takes.
-- When an artist hums/mumbles a melody before words exist, this table holds
-- the Gemini-generated word suggestions, hook candidates, and themes.
-- =============================================================================

CREATE TABLE IF NOT EXISTS track_mumble_analyses (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id             INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,

    -- Detection
    is_mumble            INTEGER NOT NULL DEFAULT 1,  -- 0=false, 1=true
    mumble_confidence    REAL,                        -- 0.0–1.0

    -- Rhythm / structure
    rhythm_description   TEXT,
    global_stress_pattern TEXT,

    -- Per-segment phonetic data (JSON array of PhoneticSegment)
    segments             TEXT,

    -- Creative output
    potential_themes     TEXT,   -- JSON array of strings
    hook_candidates      TEXT,   -- JSON array of strings
    melodic_notes        TEXT,
    vowel_palette        TEXT,   -- JSON array of strings

    model_used           TEXT DEFAULT 'gemini-2.5-pro',
    analyzed_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(track_id)
);

-- Add is_mumble flag to existing track_lyrics table
ALTER TABLE track_lyrics ADD COLUMN is_mumble INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_mumble_analyses_track_id ON track_mumble_analyses(track_id);
CREATE INDEX IF NOT EXISTS idx_track_lyrics_is_mumble ON track_lyrics(is_mumble);
