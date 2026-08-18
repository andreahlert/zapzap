import os
import sqlite3
import stat
import tempfile
import unittest

from zapzap.features.chat_sync.db import ChatSyncDB, SCHEMA_VERSION


class ChatSyncDBSchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "data", "messages.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_initialize_creates_tables_and_version(self):
        db = ChatSyncDB(self.db_path)
        db.initialize()
        try:
            names = {
                row[0]
                for row in db.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertEqual({"messages", "chats", "meta"} & names,
                             {"messages", "chats", "meta"})
            version = db.conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()[0]
            self.assertEqual(int(version), SCHEMA_VERSION)
        finally:
            db.close()

    def test_initialize_sets_restrictive_permissions(self):
        db = ChatSyncDB(self.db_path)
        db.initialize()
        db.close()
        file_mode = stat.S_IMODE(os.stat(self.db_path).st_mode)
        dir_mode = stat.S_IMODE(os.stat(os.path.dirname(self.db_path)).st_mode)
        self.assertEqual(file_mode, 0o600)
        self.assertEqual(dir_mode, 0o700)


class ChatSyncDBWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ChatSyncDB(os.path.join(self.tmp.name, "d", "m.db"))
        self.db.initialize()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _row(self, **over):
        row = {
            "id": "m1", "chat_id": "c1@x", "chat_name": "Alice",
            "sender_id": "c1@x", "sender_name": "Alice", "ts": 100,
            "type": "chat", "body": "hi", "caption": "", "from_me": 0,
            "mimetype": "", "filename": "",
        }
        row.update(over)
        return row

    def test_upsert_is_idempotent_by_id(self):
        self.db.upsert_messages([self._row(), self._row(body="edited")])
        count = self.db.conn.execute(
            "SELECT COUNT(*) FROM messages").fetchone()[0]
        body = self.db.conn.execute(
            "SELECT body FROM messages WHERE id='m1'").fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(body, "edited")

    def test_upsert_preserves_existing_media_path(self):
        self.db.upsert_messages([self._row(type="image")])
        self.db.set_media_path("m1", "/media/00001.jpg", "image/jpeg",
                               "00001.jpg")
        # A later live re-sync of the same message carries no media_path.
        self.db.upsert_messages([self._row(type="image", body="again")])
        path = self.db.conn.execute(
            "SELECT media_path FROM messages WHERE id='m1'").fetchone()[0]
        self.assertEqual(path, "/media/00001.jpg")

    def test_upsert_preserves_all_media_columns_via_coalesce(self):
        # Insert initial message, set media, then re-sync with fresh content.
        self.db.upsert_messages([self._row(type="image")])
        self.db.set_media_path("m1", "/media/00001.jpg", "image/jpeg",
                               "00001.jpg")
        # Re-sync the same message with updated body and caption; media must be preserved.
        self.db.upsert_messages([self._row(type="image", body="updated", caption="new caption")])
        row = self.db.conn.execute(
            "SELECT media_path, media_mime, media_filename FROM messages WHERE id='m1'").fetchone()
        self.assertEqual(row["media_path"], "/media/00001.jpg")
        self.assertEqual(row["media_mime"], "image/jpeg")
        self.assertEqual(row["media_filename"], "00001.jpg")

    def test_chat_cursor_roundtrip(self):
        self.assertIsNone(self.db.get_chat_cursor("c1@x"))
        self.db.set_chat("c1@x", "Alice", 0, 200, "m9", 1)
        cursor = self.db.get_chat_cursor("c1@x")
        self.assertEqual(cursor["last_synced_ts"], 200)
        self.assertEqual(cursor["last_synced_id"], "m9")
        self.assertEqual(cursor["backfill_done"], 1)
        self.assertEqual(cursor["is_group"], 0)

        self.db.set_chat("g1@x", "Group", 1, 300, "m10", 1)
        group_cursor = self.db.get_chat_cursor("g1@x")
        self.assertEqual(group_cursor["is_group"], 1)


class ChatSyncDBReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ChatSyncDB(os.path.join(self.tmp.name, "d", "m.db"))
        self.db.initialize()
        base = {"chat_id": "c1", "chat_name": "Alice", "sender_id": "c1",
                "sender_name": "Alice", "type": "chat", "caption": "",
                "from_me": 0, "mimetype": "", "filename": ""}
        self.db.upsert_messages([
            {**base, "id": "a", "ts": 100, "body": "hello world"},
            {**base, "id": "b", "ts": 200, "body": "goodbye"},
        ])
        self.db.set_chat("c1", "Alice", 0, 200, "b", 1)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_list_chats_reports_count_and_last_ts(self):
        chats = self.db.list_chats()
        self.assertEqual(len(chats), 1)
        self.assertEqual(chats[0]["message_count"], 2)
        self.assertEqual(chats[0]["last_ts"], 200)

    def test_get_messages_since_filters_and_orders(self):
        rows = self.db.get_messages("c1", since_ts=100)
        self.assertEqual([r["id"] for r in rows], ["b"])

    def test_search_matches_body(self):
        rows = self.db.search("hello")
        self.assertEqual([r["id"] for r in rows], ["a"])

    def test_stats_totals(self):
        stats = self.db.stats()
        self.assertEqual(stats["message_count"], 2)
        self.assertEqual(stats["chat_count"], 1)
        self.assertEqual(stats["last_ts"], 200)
