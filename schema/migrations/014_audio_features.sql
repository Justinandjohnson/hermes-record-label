-- Migration 014: track-level audio features extracted by librosa.
--
-- One row per track. Re-running feature extraction updates the existing row
-- (INSERT OR REPLACE). All columns nullable so a partial extraction that
-- succeeds on BPM but fails on key still persists what it got.

CREATE TABLE IF NOT EXISTS track_audio_features (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id        INTEGER NOT NULL REFERENCES tracks (id) ON DELETE CASCADE,

    -- Rhythm
    bpm             REAL,                   -- beats per minute (librosa.beat.beat_track)
    beat_count      INTEGER,                -- total beats detected
    time_signature  TEXT,                   -- estimated: "4/4" | "3/4" | "6/8" | etc.

    -- Tonality
    musical_key     TEXT,                   -- e.g. "C major" | "A minor"
    key_confidence  REAL,                   -- 0–1, how strongly the key profile matches

    -- Spectral texture
    spectral_centroid_mean  REAL,           -- mean brightness (Hz) across the track
    spectral_rolloff_mean   REAL,           -- Hz below which 85 % of energy falls

    -- Dynamics
    loudness_rms    REAL,                   -- mean RMS energy (0–1 scale)
    dynamic_range_db REAL,                  -- peak-to-floor RMS difference in dB

    analyzed_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_audio_features_track
    ON track_audio_features (track_id);
