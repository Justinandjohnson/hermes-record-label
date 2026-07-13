-- Migration 015: extended audio features — 10 additional librosa metrics,
-- madmom RNN beat analysis, and PANNs CNN14 deep-audio embeddings.
--
-- All new columns are nullable so that existing rows (written by migration 014)
-- remain valid until re-extraction fills them in.

-- ── Additional librosa features ───────────────────────────────────────────

ALTER TABLE track_audio_features ADD COLUMN
    mode TEXT;                      -- "major" | "minor"

ALTER TABLE track_audio_features ADD COLUMN
    hpss_harmonic_ratio REAL;       -- harmonic energy / total energy (0–1)

ALTER TABLE track_audio_features ADD COLUMN
    onset_density REAL;             -- onset events per second

ALTER TABLE track_audio_features ADD COLUMN
    tempo_variability REAL;         -- std(IBI) / mean(IBI); 0=rigid, higher=loose

ALTER TABLE track_audio_features ADD COLUMN
    pulse_clarity REAL;             -- peak tempogram strength (0–1 normalised)

ALTER TABLE track_audio_features ADD COLUMN
    spectral_flatness_mean REAL;    -- 0=tonal/pure tone, 1=flat/noise-like

ALTER TABLE track_audio_features ADD COLUMN
    spectral_contrast_mean REAL;    -- mean contrast across 7 sub-bands (dB)

ALTER TABLE track_audio_features ADD COLUMN
    zero_crossing_rate_mean REAL;   -- mean ZCR across frames

ALTER TABLE track_audio_features ADD COLUMN
    tonnetz_mean TEXT;              -- JSON 6-element array (tonal centroid)

ALTER TABLE track_audio_features ADD COLUMN
    mfcc_mean TEXT;                 -- JSON 13-element array (MFCC means)

ALTER TABLE track_audio_features ADD COLUMN
    mfcc_var TEXT;                  -- JSON 13-element array (MFCC variances)

-- ── madmom RNN beat features ──────────────────────────────────────────────

ALTER TABLE track_audio_features ADD COLUMN
    madmom_bpm REAL;                -- RNN beat tracker estimated BPM

ALTER TABLE track_audio_features ADD COLUMN
    madmom_beat_confidence REAL;    -- mean activation strength at beat positions

ALTER TABLE track_audio_features ADD COLUMN
    madmom_downbeat_count INTEGER;  -- downbeats detected (bar count)

ALTER TABLE track_audio_features ADD COLUMN
    madmom_swing_ratio REAL;        -- ratio of odd/even sub-beat IBIs (1.0=straight)

-- ── PANNs CNN14 deep-audio embeddings ────────────────────────────────────

CREATE TABLE IF NOT EXISTS track_audio_embeddings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id    INTEGER NOT NULL REFERENCES tracks (id) ON DELETE CASCADE,
    model       TEXT    NOT NULL DEFAULT 'CNN14',    -- model name for future variants
    embedding   BLOB    NOT NULL,                    -- 2048 float32 values, raw bytes
    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_audio_embeddings_track_model
    ON track_audio_embeddings (track_id, model);
