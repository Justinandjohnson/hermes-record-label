-- =============================================================================
-- 013_verdict_segments_wavevault_artwork.sql
--
-- Four additions, all independent of one another:
--   1. roundtable_verdicts — Dez's structured close-of-meeting decision
--   2. track_segments      — second-pass structural analysis with visual anchors
--   3. wave_vault          — curated loops/stems separate from the song vault
--   4. artwork_generations — Maren's NanoBanana attempts and the picked variant
-- =============================================================================

-- ── 1. Roundtable verdicts ────────────────────────────────────────────────
-- Dez synthesizes everyone's observations into a single decision the user
-- can act on. The "active" verdict is the row with superseded_at IS NULL.
-- A new verdict supersedes the old one rather than replacing in place, so
-- the conversation is auditable.

CREATE TABLE IF NOT EXISTS roundtable_verdicts (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id             INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,

    -- One of: SHIP, REVISE, VAULT, MINE_FOR_LOOPS
    recommendation       TEXT    NOT NULL,

    headline             TEXT    NOT NULL,
    reasoning            TEXT    NOT NULL,

    -- next_action drives the CTA shown on the roundtable canvas
    --   approve            — transition to APPROVED
    --   request_revision   — transition to FEEDBACK_GIVEN
    --   vault              — vault the track
    --   wave_vault         — extract loops to wave_vault (payload lists segments)
    next_action_kind     TEXT    NOT NULL,
    next_action_payload  TEXT,                          -- JSON

    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    superseded_at        TIMESTAMP                       -- set when a newer verdict lands
);

-- Only one active verdict per track at a time
CREATE UNIQUE INDEX IF NOT EXISTS idx_roundtable_verdicts_active
    ON roundtable_verdicts (track_id)
    WHERE superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_roundtable_verdicts_track
    ON roundtable_verdicts (track_id, created_at DESC);


-- ── 2. Track segments ─────────────────────────────────────────────────────
-- Structural segmentation. Sections are detected by energy/timbre changes,
-- not fixed time windows. Each row carries everything an agent needs to
-- point at a specific moment when giving feedback, plus a visual_anchor
-- that feeds Maren's artwork pipeline.

CREATE TABLE IF NOT EXISTS track_segments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id          INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,

    start_sec         REAL    NOT NULL,
    end_sec           REAL    NOT NULL,

    section_label     TEXT,                              -- intro/verse/chorus/drop/bridge/outro/freeform
    energy            INTEGER,                           -- 1–10
    elements_present  TEXT,                              -- JSON array: ["vocal lead", "sub bass", ...]

    mood              TEXT,
    production_notes  TEXT,

    standout          INTEGER NOT NULL DEFAULT 0,        -- 0/1: is this a moment?
    standout_reason   TEXT,                              -- nullable, only if standout=1

    -- One-line image-language description of how this segment FEELS visually.
    -- This is the bridge into Maren's NanoBanana prompts.
    visual_anchor     TEXT,

    model_used        TEXT,
    analyzed_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_track_segments_track_start
    ON track_segments (track_id, start_sec);

CREATE INDEX IF NOT EXISTS idx_track_segments_standout
    ON track_segments (track_id, standout)
    WHERE standout = 1;


-- ── 3. Wave Vault ─────────────────────────────────────────────────────────
-- Curated loops and stems pulled out of tracks. Independent of the song
-- vault: a track can be vaulted (whole-song shelved) while its vocal stem
-- lives on in the wave vault for reuse.
--
-- A row with start_sec/end_sec NULL means the whole stem is saved.
-- A row with start_sec/end_sec set means a specific loop region.

CREATE TABLE IF NOT EXISTS wave_vault (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id        INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,

    stem            TEXT    NOT NULL,                   -- vocals|drums|bass|other|full
    start_sec       REAL,                                -- nullable
    end_sec         REAL,                                -- nullable

    bpm             REAL,
    musical_key     TEXT,

    tags            TEXT,                                -- JSON array
    notes           TEXT,

    added_by        TEXT,                                -- agent name or 'user'
    added_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_wave_vault_track
    ON wave_vault (track_id);

CREATE INDEX IF NOT EXISTS idx_wave_vault_match
    ON wave_vault (musical_key, bpm);

CREATE INDEX IF NOT EXISTS idx_wave_vault_stem
    ON wave_vault (stem);


-- ── 4. Artwork generations ────────────────────────────────────────────────
-- Every NanoBanana variant Maren produces gets a row. The user picks one
-- (sets picked=1); the rest remain for audit and learning.

CREATE TABLE IF NOT EXISTS artwork_generations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id        INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,

    -- The brief Maren wrote before generating this round. Multiple variants
    -- share a brief; we store it on every row so the row is self-contained.
    brief           TEXT    NOT NULL,

    -- The prompt actually sent to NanoBanana
    prompt          TEXT    NOT NULL,

    -- Which axis this variant diverges on relative to its siblings
    -- medium|vantage|era|abstraction
    variant_axis    TEXT,

    -- Maren's one-sentence "why this variant" for the user
    rationale       TEXT,

    model           TEXT    NOT NULL,                   -- nano-banana-pro | nano-banana-2
    image_url       TEXT,                                -- nullable until generation completes

    picked          INTEGER NOT NULL DEFAULT 0,         -- 0/1: user selected this one

    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_artwork_gen_track
    ON artwork_generations (track_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_artwork_gen_picked
    ON artwork_generations (track_id, picked)
    WHERE picked = 1;
