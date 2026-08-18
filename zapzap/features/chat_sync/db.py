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
            " body, caption, from_me, media_path, media_mime, media_filename) "
            "VALUES (:id, :chat_id, :chat_name, :sender_id, :sender_name, "
            " :ts, :type, :body, :caption, :from_me, :media_path, :media_mime, "
            " :media_filename) "
            "ON CONFLICT(id) DO UPDATE SET "
            " chat_id=excluded.chat_id, chat_name=excluded.chat_name, "
            " sender_id=excluded.sender_id, sender_name=excluded.sender_name, "
            " ts=excluded.ts, type=excluded.type, body=excluded.body, "
            " caption=excluded.caption, from_me=excluded.from_me, "
            " media_path=COALESCE(excluded.media_path, messages.media_path), "
            " media_mime=COALESCE(excluded.media_mime, messages.media_mime), "
            " media_filename=COALESCE(excluded.media_filename, messages.media_filename)"
        )
        payload = [
            {**{key: row.get(key) for key in self._MESSAGE_COLUMNS},
             "media_path": None, "media_mime": None, "media_filename": None}
            for row in rows
        ]
        self.conn.executemany(sql, payload)
        self.conn.commit()

    def get_chat_cursor(self, chat_id):
        row = self.conn.execute(
            "SELECT is_group, last_synced_ts, last_synced_id, backfill_done "
            "FROM chats WHERE id = ?",
            (chat_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "is_group": row["is_group"],
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

    @staticmethod
    def _message_dict(row):
        return {
            "id": row["id"], "chat_id": row["chat_id"],
            "chat_name": row["chat_name"], "sender_id": row["sender_id"],
            "sender_name": row["sender_name"], "ts": row["ts"],
            "type": row["type"], "body": row["body"],
            "caption": row["caption"], "from_me": row["from_me"],
            "media_path": row["media_path"], "media_mime": row["media_mime"],
            "media_filename": row["media_filename"],
        }

    def list_chats(self):
        rows = self.conn.execute(
            "SELECT c.id AS id, c.name AS name, c.is_group AS is_group, "
            " COUNT(m.id) AS message_count, MAX(m.ts) AS last_ts "
            "FROM chats c LEFT JOIN messages m ON m.chat_id = c.id "
            "GROUP BY c.id ORDER BY last_ts DESC"
        ).fetchall()
        return [
            {"id": r["id"], "name": r["name"], "is_group": r["is_group"],
             "message_count": r["message_count"], "last_ts": r["last_ts"]}
            for r in rows
        ]

    def get_messages(self, chat_id, since_ts=None, limit=100, offset=0):
        if since_ts is None:
            rows = self.conn.execute(
                "SELECT * FROM messages WHERE chat_id=? "
                "ORDER BY ts ASC LIMIT ? OFFSET ?",
                (chat_id, limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM messages WHERE chat_id=? AND ts > ? "
                "ORDER BY ts ASC LIMIT ? OFFSET ?",
                (chat_id, since_ts, limit, offset),
            ).fetchall()
        return [self._message_dict(r) for r in rows]

    def search(self, query, chat_id=None, limit=100):
        like = "%" + query + "%"
        if chat_id is None:
            rows = self.conn.execute(
                "SELECT * FROM messages WHERE body LIKE ? OR caption LIKE ? "
                "ORDER BY ts DESC LIMIT ?",
                (like, like, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM messages WHERE chat_id=? AND "
                "(body LIKE ? OR caption LIKE ?) ORDER BY ts DESC LIMIT ?",
                (chat_id, like, like, limit),
            ).fetchall()
        return [self._message_dict(r) for r in rows]

    def stats(self):
        message_count = self.conn.execute(
            "SELECT COUNT(*) FROM messages").fetchone()[0]
        chat_count = self.conn.execute(
            "SELECT COUNT(*) FROM chats").fetchone()[0]
        last_ts = self.conn.execute(
            "SELECT MAX(ts) FROM messages").fetchone()[0]
        return {"message_count": message_count, "chat_count": chat_count,
                "last_ts": last_ts}
