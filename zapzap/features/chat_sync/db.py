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

    _MESSAGE_COLUMNS = (
        "id", "chat_id", "chat_name", "sender_id", "sender_name", "ts",
        "type", "body", "caption", "from_me",
    )

    def upsert_messages(self, rows) -> None:
        if not rows:
            return
        sql = (
            "INSERT INTO messages "
            "(id, chat_id, chat_name, sender_id, sender_name, ts, type, "
            " body, caption, from_me) "
            "VALUES (:id, :chat_id, :chat_name, :sender_id, :sender_name, "
            " :ts, :type, :body, :caption, :from_me) "
            "ON CONFLICT(id) DO UPDATE SET "
            " chat_id=excluded.chat_id, chat_name=excluded.chat_name, "
            " sender_id=excluded.sender_id, sender_name=excluded.sender_name, "
            " ts=excluded.ts, type=excluded.type, body=excluded.body, "
            " caption=excluded.caption, from_me=excluded.from_me"
        )
        payload = [
            {key: row.get(key) for key in self._MESSAGE_COLUMNS}
            for row in rows
        ]
        self.conn.executemany(sql, payload)
        self.conn.commit()

    def get_chat_cursor(self, chat_id):
        row = self.conn.execute(
            "SELECT last_synced_ts, last_synced_id, backfill_done "
            "FROM chats WHERE id = ?",
            (chat_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "last_synced_ts": row["last_synced_ts"],
            "last_synced_id": row["last_synced_id"],
            "backfill_done": row["backfill_done"],
        }

    def set_chat(self, chat_id, name, is_group, last_synced_ts,
                 last_synced_id, backfill_done) -> None:
        self.conn.execute(
            "INSERT INTO chats "
            "(id, name, is_group, last_synced_ts, last_synced_id, "
            " backfill_done) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            " name=excluded.name, is_group=excluded.is_group, "
            " last_synced_ts=excluded.last_synced_ts, "
            " last_synced_id=excluded.last_synced_id, "
            " backfill_done=excluded.backfill_done",
            (chat_id, name, is_group, last_synced_ts, last_synced_id,
             backfill_done),
        )
        self.conn.commit()

    def set_media_path(self, message_id, media_path, media_mime,
                       media_filename) -> None:
        self.conn.execute(
            "UPDATE messages SET media_path=?, media_mime=?, "
            "media_filename=? WHERE id=?",
            (media_path, media_mime, media_filename, message_id),
        )
        self.conn.commit()
