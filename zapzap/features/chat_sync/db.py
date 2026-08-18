"""SQLite store for captured WhatsApp messages.

Qt-free by design: the caller resolves the database path (from
QStandardPaths at runtime, or a temp dir in tests) and passes it in.
"""

import os
import sqlite3


SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT,
    chat_name TEXT,
    sender_id TEXT,
    sender_name TEXT,
    ts INTEGER,
    type TEXT,
    body TEXT,
    caption TEXT,
    from_me INTEGER,
    media_path TEXT,
    media_mime TEXT,
    media_filename TEXT
);
CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    name TEXT,
    is_group INTEGER,
    last_synced_ts INTEGER,
    last_synced_id TEXT,
    backfill_done INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_chat_ts
    ON messages (chat_id, ts);
"""


class ChatSyncDB:
    """Own the SQLite connection and schema for the sync store."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None

    def initialize(self) -> None:
        directory = os.path.dirname(self.db_path)
        os.makedirs(directory, exist_ok=True)
        os.chmod(directory, 0o700)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()
        os.chmod(self.db_path, 0o600)

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
