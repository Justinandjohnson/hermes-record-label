-- 009_cloud_vault.sql
-- Cloud vault tables: file versions, timestamp comments, sync audit log

CREATE TABLE IF NOT EXISTS file_versions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id     INTEGER NOT NULL REFERENCES tracks(id),
    version_num  INTEGER NOT NULL DEFAULT 1,
    file_path    TEXT NOT NULL,
    b2_key       TEXT,
    b2_bucket    TEXT,
    file_hash    TEXT NOT NULL,
    file_size    INTEGER,
    label        TEXT,
    dvc_tag      TEXT,
    uploaded_at  TEXT,
    created_at   TEXT DEFAULT (datetime('now')),
    UNIQUE(track_id, version_num)
);

CREATE TABLE IF NOT EXISTS track_comments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id     INTEGER NOT NULL REFERENCES tracks(id),
    version_id   INTEGER REFERENCES file_versions(id),
    timestamp_s  REAL,
    author       TEXT NOT NULL,
    body         TEXT NOT NULL,
    resolved     INTEGER DEFAULT 0,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cloud_sync_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    operation    TEXT NOT NULL,
    b2_key       TEXT,
    file_path    TEXT,
    status       TEXT NOT NULL,
    error        TEXT,
    bytes        INTEGER,
    duration_ms  INTEGER,
    synced_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS batch_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id     TEXT NOT NULL UNIQUE,
    agent        TEXT NOT NULL,
    task_type    TEXT NOT NULL,
    status       TEXT DEFAULT 'pending',
    submitted_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    result       TEXT
);

CREATE INDEX IF NOT EXISTS idx_file_versions_track  ON file_versions(track_id);
CREATE INDEX IF NOT EXISTS idx_track_comments_track ON track_comments(track_id);
CREATE INDEX IF NOT EXISTS idx_track_comments_ts    ON track_comments(track_id, timestamp_s);
CREATE INDEX IF NOT EXISTS idx_cloud_sync_log_op    ON cloud_sync_log(operation, synced_at);
CREATE INDEX IF NOT EXISTS idx_batch_jobs_status    ON batch_jobs(status);
