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
