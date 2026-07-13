-- ============================================================================
-- 006_catalog.sql — Bandcamp catalog importer
--
-- Adds Bandcamp metadata to projects and tracks so the artist's existing
-- released catalog can be imported and studied by the agents.
-- ============================================================================

-- ── Project-level Bandcamp metadata ──────────────────────────────────────
ALTER TABLE projects ADD COLUMN bandcamp_url TEXT;
ALTER TABLE projects ADD COLUMN bandcamp_id TEXT;
ALTER TABLE projects ADD COLUMN release_date TEXT;
ALTER TABLE projects ADD COLUMN bandcamp_tags TEXT;          -- JSON array
ALTER TABLE projects ADD COLUMN bandcamp_description TEXT;
ALTER TABLE projects ADD COLUMN cover_art_url TEXT;

-- ── Track-level Bandcamp metadata ────────────────────────────────────────
ALTER TABLE tracks ADD COLUMN bandcamp_track_url TEXT;
ALTER TABLE tracks ADD COLUMN track_number INTEGER;
ALTER TABLE tracks ADD COLUMN bandcamp_streaming_url TEXT;

-- ── Catalog import audit table ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS catalog_imports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT    NOT NULL DEFAULT 'bandcamp',
    artist_url      TEXT    NOT NULL,
    imported_at     TEXT    DEFAULT (datetime('now')),
    album_count     INTEGER DEFAULT 0,
    track_count     INTEGER DEFAULT 0,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_projects_bandcamp_id
    ON projects (bandcamp_id);

CREATE INDEX IF NOT EXISTS idx_tracks_bandcamp_url
    ON tracks (bandcamp_track_url);
