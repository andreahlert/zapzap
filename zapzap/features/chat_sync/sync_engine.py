# zapzap/features/chat_sync/sync_engine.py
"""Drive continuous capture of the active account's WhatsApp messages.

Reuses the export feature's WA-JS provider and polling pattern. On start it
injects WA-JS plus the sync helper, waits for the engine, backfills or
reconciles each chat against a stored cursor, then drains live messages on a
timer. Media is downloaded one item at a time by a throttled queue.
"""

import json
import logging
import os

from gettext import gettext as _

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from zapzap.features.export_chat import media_writer
from zapzap.features.export_chat.wa_js_provider import WaJsProvider
from zapzap.features.chat_sync import paths
from zapzap.features.chat_sync.db import ChatSyncDB
from zapzap.features.chat_sync.sync_logic import select_new_rows, next_cursor


logger = logging.getLogger(__name__)

POLL_INTERVAL_MS = 500
ENGINE_READY_TIMEOUT_MS = 30_000
TASK_TIMEOUT_MS = 120_000
PAGE_SIZE = 100
LIVE_DRAIN_INTERVAL_MS = 1000


class ChatSyncEngine(QObject):
    """Capture messages from one account page into the SQLite store."""

    state_changed = pyqtSignal(str)

    def __init__(self, page, parent=None):
        super().__init__(parent)
        self._page = page
        self._db = None
        self._provider = WaJsProvider(self)
        self._provider.ready.connect(self._on_bundle_ready)
        self._provider.failed.connect(self._on_bundle_failed)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_tick)
        self._poll_handler = None
        self._poll_elapsed_ms = 0
        self._poll_timeout_ms = 0
        self._poll_generation = 0

        self._live_timer = QTimer(self)
        self._live_timer.setInterval(LIVE_DRAIN_INTERVAL_MS)
        self._live_timer.timeout.connect(self._drain_live)

        self._running = False
        self._chats = []
        self._chat_index = 0
        self._page_cursor = None
        self._media_queue = []

    # === Lifecycle ===

    def start(self):
        if self._running:
            return
        self._running = True
        self.state_changed.emit("syncing")
        self._db = ChatSyncDB(paths.db_path())
        self._db.initialize()
        self._provider.ensure()

    def stop(self):
        if not self._running:
            return
        self._running = False
        self._live_timer.stop()
        self._stop_polling()
        self._provider.abort()
        if self._db is not None:
            self._db.close()
            self._db = None
        self.state_changed.emit("idle")

    def _on_bundle_failed(self, message):
        logger.error("chat_sync WA-JS download failed: %s", message)
        self.state_changed.emit("error")
        self.stop()

    def _on_bundle_ready(self, bundle_js):
        if not self._running:
            return
        self._page.runJavaScript(
            "if (typeof window.WPP === 'undefined') {\n" + bundle_js + "\n}")
        self._page.runJavaScript(self._helper_source())
        self._wait_engine_ready()

    def _wait_engine_ready(self):
        generation = self._new_generation()

        def tick():
            self._page.runJavaScript(
                "!!(window._zapzapSync && "
                "window._zapzapSync.isEngineReady())",
                lambda ready: self._on_ready(ready, generation))

        self._start_polling(tick, ENGINE_READY_TIMEOUT_MS)

    def _on_ready(self, ready, generation):
        if not self._running or generation != self._poll_generation:
            return
        if not ready:
            return
        self._stop_polling()
        self._page.runJavaScript(
            "window._zapzapSync.startLive({})".format(json.dumps(_("You"))))
        self._page.runJavaScript("window._zapzapSync.listChats()")
        self._start_task_polling(
            self._on_chats_listed,
            on_error=lambda message: self._on_list_failed(message))

    # === Reconcile / backfill ===

    def _on_chats_listed(self, chats):
        self._chats = chats or []
        self._chat_index = 0
        self._sync_next_chat()

    def _on_list_failed(self, message):
        logger.warning("chat_sync listChats failed: %s", message)
        self._start_live()

    def _sync_next_chat(self):
        if not self._running:
            return
        if self._chat_index >= len(self._chats):
            self._start_live()
            return
        chat = self._chats[self._chat_index]
        # Newest (ts, id) seen while paging this chat; set from the first page.
        self._page_cursor = None
        self._page.runJavaScript(
            "window._zapzapSync.getMessagesSince({}, {}, null)".format(
                json.dumps(chat["id"]), PAGE_SIZE))
        self._start_task_polling(
            lambda rows: self._on_chat_page(chat, rows),
            on_error=lambda message: self._on_chat_page_failed(chat, message))

    def _on_chat_page(self, chat, rows):
        # rows are newest-first; rows[-1] is the oldest of this page.
        rows = rows or []
        cursor = self._db.get_chat_cursor(chat["id"])
        backfill_done = cursor["backfill_done"] if cursor else 0
        last_ts = cursor["last_synced_ts"] if cursor else None
        last_id = cursor["last_synced_id"] if cursor else None
        is_group = 1 if chat.get("isGroup") else 0

        if backfill_done:
            # Delta: store only messages newer than the stored cursor and
            # keep paging older until the cursor is reached.
            to_store, reached = select_new_rows(rows, last_ts, last_id)
        else:
            # Backfill: store every page and page older until exhausted.
            to_store, reached = rows, False

        if to_store:
            for row in to_store:
                row["chat_id"] = chat["id"]
                row["chat_name"] = chat["name"]
            self._db.upsert_messages(to_store)
            self._enqueue_media(to_store)

        # The newest message of the whole run is on the first page.
        if self._page_cursor is None and rows:
            self._page_cursor = next_cursor(rows, (last_ts or 0, last_id))

        # A full page may have more behind it; a short page ends the walk.
        if rows and not reached and len(rows) >= PAGE_SIZE:
            oldest_id = rows[-1]["id"]
            self._page.runJavaScript(
                "window._zapzapSync.getMessagesSince({}, {}, {})".format(
                    json.dumps(chat["id"]), PAGE_SIZE, json.dumps(oldest_id)))
            self._start_task_polling(
                lambda more: self._on_chat_page(chat, more),
                on_error=lambda message: self._on_chat_page_failed(
                    chat, message))
            return

        final_ts, final_id = self._page_cursor or (last_ts or 0, last_id)
        self._db.set_chat(chat["id"], chat["name"], is_group,
                          final_ts, final_id, 1)
        self._chat_index += 1
        self._sync_next_chat()

    def _on_chat_page_failed(self, chat, message):
        logger.warning("chat_sync getMessagesSince failed for %s: %s",
                       chat.get("id"), message)
        self._chat_index += 1
        self._sync_next_chat()

    # === Media ===

    def _enqueue_media(self, rows):
        for row in rows:
            if media_writer.is_downloadable(row) and row.get("id"):
                self._media_queue.append(row)

    def _drain_media(self):
        if not self._running or not self._media_queue:
            return
        row = self._media_queue.pop(0)
        self._page.runJavaScript(
            "window._zapzapSync.downloadMedia({})".format(
                json.dumps(row["id"])))
        self._start_task_polling(
            lambda data_url: self._on_media(row, data_url),
            on_error=lambda message: self._on_media_error(row, message))

    def _on_media(self, row, data_url):
        if data_url:
            try:
                ext = media_writer.guess_extension(row.get("mimetype", ""))
                safe_id = row["id"].replace("/", "_").replace(":", "_")
                filename = safe_id + ext
                directory = paths.media_dir()
                os.makedirs(directory, exist_ok=True)
                os.chmod(directory, 0o700)
                media_writer.write_media(directory, filename, data_url)
                self._db.set_media_path(
                    row["id"], os.path.join(directory, filename),
                    row.get("mimetype", ""), filename)
            except (OSError, ValueError):
                logger.exception("Failed to save synced media")
        self._drain_media()

    def _on_media_error(self, row, message):
        logger.warning("Media sync failed for %s: %s", row.get("id"), message)
        self._drain_media()

    # === Live ===

    def _start_live(self):
        self._live_timer.start()
        self._drain_media()

    def _drain_live(self):
        if not self._running:
            return
        self._page.runJavaScript(
            "window._zapzapSync ? window._zapzapSync.drainPending() : []",
            self._on_live_rows)

    def _on_live_rows(self, rows):
        if not rows or not self._running:
            return
        self._db.upsert_messages(rows)
        for row in rows:
            cur = self._db.get_chat_cursor(row["chat_id"])
            self._db.set_chat(
                row["chat_id"], row.get("chat_name") or row["chat_id"],
                cur["is_group"] if cur else 0, row["ts"], row["id"],
                cur["backfill_done"] if cur else 0)
        self._enqueue_media(rows)
        self._drain_media()

    # === Polling (mirrors export_controller) ===

    def _start_task_polling(self, on_result, on_error=None):
        generation = self._new_generation()

        def tick():
            self._page.runJavaScript(
                "window._zapzapSync ? window._zapzapSync.poll() : null",
                lambda outcome: self._on_task_poll(
                    outcome, on_result, on_error, generation))

        self._start_polling(tick, TASK_TIMEOUT_MS)

    def _on_task_poll(self, outcome, on_result, on_error, generation):
        if not self._running or generation != self._poll_generation:
            return
        if outcome is None:
            self._stop_polling()
            return
        status = outcome.get("status")
        if status == "done":
            self._stop_polling()
            on_result(outcome.get("result"))
        elif status == "error":
            self._stop_polling()
            if on_error is not None:
                on_error(outcome.get("error"))
            else:
                logger.warning("chat_sync page error: %s",
                               outcome.get("error"))

    def _new_generation(self) -> int:
        self._poll_generation += 1
        return self._poll_generation

    def _start_polling(self, handler, timeout_ms):
        self._poll_handler = handler
        self._poll_elapsed_ms = 0
        self._poll_timeout_ms = timeout_ms
        self._poll_timer.start()

    def _poll_tick(self):
        self._poll_elapsed_ms += POLL_INTERVAL_MS
        if self._poll_elapsed_ms > self._poll_timeout_ms:
            self._stop_polling()
            return
        if self._poll_handler is not None:
            self._poll_handler()

    def _stop_polling(self):
        self._poll_timer.stop()
        self._poll_handler = None

    @staticmethod
    def _helper_source() -> str:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        js_path = os.path.join(base_dir, "scripts", "chat_sync.js")
        with open(js_path, "r", encoding="utf-8") as helper:
            return helper.read()
