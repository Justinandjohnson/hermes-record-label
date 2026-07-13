-- Migration 003: Conductor message queue
-- Agents submit drafts here. Studio conductor reviews, reasons, and delivers.

CREATE TABLE IF NOT EXISTS pending_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent   TEXT    NOT NULL,          -- 'nico', 'diane', 'mika', 'rex'
    draft        TEXT    NOT NULL,          -- the message the agent wants to send
    context      TEXT,                      -- why / what triggered this message
    track_id     INTEGER REFERENCES tracks(id),
    priority     TEXT    DEFAULT 'normal',  -- 'urgent', 'normal', 'low'
    submitted_at TEXT    DEFAULT (datetime('now')),
    status       TEXT    DEFAULT 'pending', -- 'pending', 'approved', 'rejected', 'needs_context'
    conductor_reasoning TEXT,               -- conductor's internal reasoning log
    refined_draft       TEXT,              -- conductor's revised version (if changed)
    sent_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_pending_messages_status
    ON pending_messages (status, submitted_at);
