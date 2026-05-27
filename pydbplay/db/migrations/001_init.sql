-- Migration 001: initial schema
-- Applied when PRAGMA user_version < 1
-- Tracked by: PRAGMA user_version (no Alembic)

CREATE TABLE IF NOT EXISTS connection_profile (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT    NOT NULL,
    engine              TEXT    NOT NULL CHECK (engine IN ('postgres', 'mysql', 'sqlite')),
    host                TEXT,
    port                INTEGER,
    database            TEXT    NOT NULL,
    username            TEXT,
    password_encrypted  TEXT,            -- Fernet-encrypted; NULL when keyring is used
    ssl_mode            TEXT,
    read_only           INTEGER NOT NULL DEFAULT 0,  -- SQLite stores bool as integer
    color               TEXT,            -- hex colour e.g. "#6366f1"
    created_at          TEXT    NOT NULL,            -- ISO-8601 UTC
    updated_at          TEXT    NOT NULL,
    last_used_at        TEXT
);

CREATE TABLE IF NOT EXISTS query_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id   INTEGER NOT NULL REFERENCES connection_profile(id) ON DELETE CASCADE,
    sql             TEXT    NOT NULL,
    executed_at     TEXT    NOT NULL,   -- ISO-8601 UTC
    duration_ms     INTEGER NOT NULL,
    row_count       INTEGER,
    success         INTEGER NOT NULL,   -- 0 or 1
    error_message   TEXT
);

CREATE TABLE IF NOT EXISTS saved_query (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id   INTEGER REFERENCES connection_profile(id) ON DELETE SET NULL,
    name            TEXT    NOT NULL,
    sql             TEXT    NOT NULL,
    description     TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

-- Indexes for common lookups
CREATE INDEX IF NOT EXISTS idx_query_history_connection_id ON query_history(connection_id);
CREATE INDEX IF NOT EXISTS idx_saved_query_connection_id   ON saved_query(connection_id);
