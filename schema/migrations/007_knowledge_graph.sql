-- 007_knowledge_graph.sql
-- SQLite-native knowledge graph: no new dependencies, uses existing FTS5 pattern

CREATE TABLE IF NOT EXISTS kg_nodes (
    id         TEXT PRIMARY KEY,
    type       TEXT NOT NULL,
    label      TEXT NOT NULL,
    properties TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS kg_edges (
    source     TEXT NOT NULL REFERENCES kg_nodes(id),
    target     TEXT NOT NULL REFERENCES kg_nodes(id),
    relation   TEXT NOT NULL,
    weight     REAL DEFAULT 1.0,
    properties TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(source, target, relation)
);

CREATE INDEX IF NOT EXISTS idx_kg_edges_source   ON kg_edges(source);
CREATE INDEX IF NOT EXISTS idx_kg_edges_target   ON kg_edges(target);
CREATE INDEX IF NOT EXISTS idx_kg_edges_relation ON kg_edges(relation);
CREATE INDEX IF NOT EXISTS idx_kg_nodes_type     ON kg_nodes(type);

CREATE VIRTUAL TABLE IF NOT EXISTS kg_nodes_fts USING fts5(
    label, type UNINDEXED,
    content='kg_nodes', content_rowid='rowid',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS kg_nodes_fts_insert AFTER INSERT ON kg_nodes BEGIN
    INSERT INTO kg_nodes_fts(rowid, label, type) VALUES (new.rowid, new.label, new.type);
END;
CREATE TRIGGER IF NOT EXISTS kg_nodes_fts_delete AFTER DELETE ON kg_nodes BEGIN
    INSERT INTO kg_nodes_fts(kg_nodes_fts, rowid, label, type)
    VALUES ('delete', old.rowid, old.label, old.type);
END;
CREATE TRIGGER IF NOT EXISTS kg_nodes_fts_update AFTER UPDATE ON kg_nodes BEGIN
    INSERT INTO kg_nodes_fts(kg_nodes_fts, rowid, label, type)
    VALUES ('delete', old.rowid, old.label, old.type);
    INSERT INTO kg_nodes_fts(rowid, label, type) VALUES (new.rowid, new.label, new.type);
END;
