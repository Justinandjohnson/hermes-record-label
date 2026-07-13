-- =============================================================================
-- Migration 011: Stem Separation & Lyrics
-- =============================================================================
-- Stores Demucs stem file paths and Gemini lyrics transcriptions per track.
-- Stems are generated on-demand by the separate_stems MCP tool and stored
-- on disk under DATA_DIR/stems/htdemucs/{track_filename}/*.wav
-- =============================================================================

-- stem paths for each separated track
CREATE TABLE IF NOT EXISTS track_stems (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    model       TEXT    NOT NULL DEFAULT 'htdemucs',
    vocals_path TEXT,          -- absolute path to vocals.wav
    drums_path  TEXT,          -- absolute path to drums.wav
    bass_path   TEXT,          -- absolute path to bass.wav
    other_path  TEXT,          -- absolute path to other.wav (instruments)
    separated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(track_id, model)
);

-- lyrics and vocal analysis from Gemini (based on vocal stem)
CREATE TABLE IF NOT EXISTS track_lyrics (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id             INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    lyrics_clean         TEXT,          -- full lyrics as plain text
    lyrics_timestamped   TEXT,          -- JSON array: [{line, start_time, end_time}]
    vocal_style          TEXT,          -- e.g. "melodic trap, autotune-heavy"
    vocal_observations   TEXT,          -- JSON array of observation strings
    language             TEXT DEFAULT 'english',
    explicit             INTEGER DEFAULT 0,  -- 0=false, 1=true (SQLite bool)
    model_used           TEXT DEFAULT 'gemini-2.5-pro',
    extracted_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(track_id)
);

-- instrumental analysis from Gemini (based on other/instrumental stem)
CREATE TABLE IF NOT EXISTS stem_instrumental_analyses (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id             INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    arrangement_summary  TEXT,
    instruments_detailed TEXT,          -- JSON array
    production_techniques TEXT,         -- JSON array
    arrangement_moments  TEXT,          -- JSON array
    frequency_balance    TEXT,
    stereo_field         TEXT,
    dynamic_range        TEXT,
    essence_elements     TEXT,          -- JSON array — Rubin's "non-negotiables"
    model_used           TEXT DEFAULT 'gemini-2.5-pro',
    analyzed_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(track_id)
);

CREATE INDEX IF NOT EXISTS idx_track_stems_track_id ON track_stems(track_id);
CREATE INDEX IF NOT EXISTS idx_track_lyrics_track_id ON track_lyrics(track_id);
CREATE INDEX IF NOT EXISTS idx_stem_inst_analyses_track_id ON stem_instrumental_analyses(track_id);
