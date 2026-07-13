-- ============================================================================
-- 005_features_tier0_tier1.sql — Vault, Sessions, QC Panel, Release Cycle,
--                                 Sync Tags, Royalty Registration,
--                                 Album-as-Statement
-- ============================================================================

-- ── Tier 0: Work Pattern Intelligence ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS session_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at        TEXT    NOT NULL,
    ended_at          TEXT,
    duration_minutes  INTEGER,
    export_count      INTEGER DEFAULT 0,
    project_id        INTEGER REFERENCES projects(id),
    calendar_event_id TEXT,
    session_note      TEXT,
    mood              TEXT,
    created_at        TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_session_log_started
    ON session_log (started_at);

CREATE TABLE IF NOT EXISTS creation_streaks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at        TEXT    NOT NULL,
    ended_at          TEXT,
    length_days       INTEGER,
    longest_gap_hours REAL,
    total_exports     INTEGER DEFAULT 0,
    created_at        TEXT    DEFAULT (datetime('now'))
);

-- ── Tier 1, Feature 1: The Vault ──────────────────────────────────────────
-- New valid track states: VAULT, VAULT_RESURFACED
-- (enforced by convention — state column is already TEXT)

-- Add vault metadata columns to tracks
-- SQLite ALTER TABLE only supports ADD COLUMN, one at a time
ALTER TABLE tracks ADD COLUMN vault_reason TEXT;
ALTER TABLE tracks ADD COLUMN vault_date TEXT;

-- ── Tier 1, Feature 2: Human QC Panel ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS listening_panel (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    phone           TEXT,
    imessage_id     TEXT,
    relationship    TEXT,
    genre_knowledge TEXT,
    active          INTEGER DEFAULT 1,
    added_at        TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS panel_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id        INTEGER NOT NULL REFERENCES tracks(id),
    status          TEXT    DEFAULT 'sent',
    sent_at         TEXT    DEFAULT (datetime('now')),
    closed_at       TEXT,
    summary         TEXT
);

CREATE INDEX IF NOT EXISTS idx_panel_sessions_track
    ON panel_sessions (track_id);

CREATE TABLE IF NOT EXISTS panel_responses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES panel_sessions(id),
    panelist_id     INTEGER NOT NULL REFERENCES listening_panel(id),
    raw_response    TEXT,
    sentiment       TEXT,
    would_buy       INTEGER,
    key_quote       TEXT,
    response_time   INTEGER,
    listened_count  INTEGER,
    received_at     TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_panel_responses_session
    ON panel_responses (session_id);

-- ── Tier 1, Feature 3: Release Cycle Planner ──────────────────────────────

-- Extend milestones table
ALTER TABLE milestones ADD COLUMN due_date TEXT;
ALTER TABLE milestones ADD COLUMN milestone_type TEXT DEFAULT 'custom';
-- milestone_type values: 'release_cycle', 'project', 'custom'

-- ── Tier 1, Feature 4: Sync Readiness Tagging ────────────────────────────

ALTER TABLE audio_analyses ADD COLUMN sync_scene_tags TEXT;
ALTER TABLE audio_analyses ADD COLUMN vocal_presence TEXT;
ALTER TABLE audio_analyses ADD COLUMN explicit_content INTEGER DEFAULT 0;
ALTER TABLE audio_analyses ADD COLUMN sync_tier TEXT;
ALTER TABLE audio_analyses ADD COLUMN isrc TEXT;

-- ── Tier 1, Feature 5: Autonomous Royalty Registration ───────────────────

CREATE TABLE IF NOT EXISTS royalty_registrations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org_name        TEXT    NOT NULL,
    org_type        TEXT    NOT NULL,
    status          TEXT    DEFAULT 'not_started',
    account_id      TEXT,
    registered_at   TEXT,
    last_checked    TEXT,
    next_check_due  TEXT,
    notes           TEXT,
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS works_registrations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id        INTEGER NOT NULL REFERENCES tracks(id),
    org_name        TEXT    NOT NULL,
    registration_id TEXT,
    status          TEXT    DEFAULT 'pending',
    isrc            TEXT,
    iswc            TEXT,
    registered_at   TEXT,
    created_at      TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_works_reg_track
    ON works_registrations (track_id);

CREATE TABLE IF NOT EXISTS royalty_news (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org_name        TEXT,
    headline        TEXT    NOT NULL,
    summary         TEXT,
    source_url      TEXT,
    relevance       TEXT,
    flagged_by      TEXT    DEFAULT 'studio',
    created_at      TEXT    DEFAULT (datetime('now'))
);

-- ── Tier 1, Feature 6: Album-as-Statement Mode ──────────────────────────

ALTER TABLE projects ADD COLUMN thematic_seed TEXT;
ALTER TABLE projects ADD COLUMN seed_set_at TEXT;

-- ── Tier 2, Feature 10: Sync Pitching Pipeline (table only) ──────────────

CREATE TABLE IF NOT EXISTS sync_submissions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id          INTEGER NOT NULL REFERENCES tracks(id),
    platform          TEXT    NOT NULL,
    status            TEXT    DEFAULT 'submitted',
    submission_date   TEXT    DEFAULT (datetime('now')),
    response_date     TEXT,
    placement_details TEXT,
    fee               REAL,
    notes             TEXT
);

CREATE INDEX IF NOT EXISTS idx_sync_submissions_track
    ON sync_submissions (track_id);
