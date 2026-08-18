"""Focused regression tests for ChatSyncEngine.

These exercise the single-flight media guard (finding 1) and the group-JID
derivation for live-first chats (finding 7) without touching a real
QWebEnginePage: a plain stub page records ``runJavaScript`` calls and
``_start_task_polling`` is stubbed to a no-op so no real timers run.
"""

import os
import tempfile
import unittest

from PyQt6.QtCore import QCoreApplication

from zapzap.features.chat_sync.db import ChatSyncDB
from zapzap.features.chat_sync.sync_engine import ChatSyncEngine


class _StubSignal:
    """Minimal stand-in for a Qt signal exposing ``connect``."""

    def connect(self, *args, **kwargs):
        pass


class _StubPage:
    """Records runJavaScript calls instead of running them on a web page."""

    def __init__(self):
        self.calls = []
        self.loadFinished = _StubSignal()

    def runJavaScript(self, code, callback=None):
        self.calls.append(code)

    def download_calls(self):
        return [code for code in self.calls if "downloadMedia" in code]


class ChatSyncEngineMediaGuardTests(unittest.TestCase):
    def setUp(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.page = _StubPage()
        self.engine = ChatSyncEngine(self.page)
        # No real polling: a stub keeps every download "in flight" until we
        # explicitly deliver the result via _on_media.
        self.engine._start_task_polling = lambda *args, **kwargs: None
        self.engine._running = True

    def tearDown(self):
        self.engine._running = False

    def test_second_drain_while_active_does_not_start_a_download(self):
        row1 = {"id": "a/1:x", "type": "image", "mimetype": "image/jpeg"}
        row2 = {"id": "b/2:y", "type": "image", "mimetype": "image/jpeg"}
        self.engine._media_queue = [row1, row2]

        self.engine._drain_media()
        self.assertTrue(self.engine._media_active)
        self.assertEqual(len(self.page.download_calls()), 1)
        self.assertEqual(len(self.engine._media_queue), 1)

        # A concurrent live-tick call must no-op while a download is in flight.
        self.engine._drain_media()
        self.assertEqual(len(self.page.download_calls()), 1)
        self.assertEqual(len(self.engine._media_queue), 1)
        self.assertTrue(self.engine._media_active)

        # Completing the first download drains the next: the queue empties and
        # a second download is issued (now the in-flight one).
        self.engine._on_media(row1, None)
        self.assertEqual(len(self.page.download_calls()), 2)
        self.assertEqual(len(self.engine._media_queue), 0)
        self.assertTrue(self.engine._media_active)

        # Completing that one with an empty queue leaves the chain idle.
        self.engine._on_media(row2, None)
        self.assertFalse(self.engine._media_active)
        self.assertEqual(len(self.page.download_calls()), 2)


class ChatSyncEngineTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.page = _StubPage()
        self.engine = ChatSyncEngine(self.page)
        self.engine._running = True

    def tearDown(self):
        self.engine._running = False
        self.engine._stop_polling()

    def test_media_timeout_releases_guard(self):
        # Set up media queue with one item
        self.engine._media_queue = [
            {"id": "m1", "type": "image", "mimetype": ""}
        ]
        # Drain first item: starts task polling and sets _media_active
        self.engine._drain_media()
        self.assertTrue(self.engine._media_active)

        # Simulate the poll never resolving: force elapsed clock past timeout
        self.engine._poll_elapsed_ms = self.engine._poll_timeout_ms + 1
        # Timeout fires on_error("timeout"), which clears _media_active guard
        self.engine._poll_tick()
        # After timeout, the guard must be released (not stuck True)
        self.assertFalse(self.engine._media_active)


class ChatSyncEngineLiveGroupTests(unittest.TestCase):
    def setUp(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.tmp = tempfile.TemporaryDirectory()
        self.page = _StubPage()
        self.engine = ChatSyncEngine(self.page)
        self.engine._start_task_polling = lambda *args, **kwargs: None
        self.engine._db = ChatSyncDB(os.path.join(self.tmp.name, "d", "m.db"))
        self.engine._db.initialize()
        self.engine._running = True

    def tearDown(self):
        self.engine._running = False
        self.engine._db.close()
        self.tmp.cleanup()

    def _live_row(self, chat_id):
        return {
            "id": "msg-" + chat_id, "chat_id": chat_id,
            "chat_name": None, "sender_id": chat_id, "sender_name": "x",
            "ts": 500, "type": "chat", "body": "hi", "caption": "",
            "from_me": 0,
        }

    def test_unknown_group_jid_stored_as_group(self):
        self.engine._on_live_rows([self._live_row("123-456@g.us")])
        cursor = self.engine._db.get_chat_cursor("123-456@g.us")
        self.assertEqual(cursor["is_group"], 1)

    def test_unknown_direct_jid_stored_as_non_group(self):
        self.engine._on_live_rows([self._live_row("999@c.us")])
        cursor = self.engine._db.get_chat_cursor("999@c.us")
        self.assertEqual(cursor["is_group"], 0)


if __name__ == "__main__":
    unittest.main()
