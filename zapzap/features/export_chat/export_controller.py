"""Orchestrate the chat export flow for the active account page.

The flow injects the WA-JS bundle plus the export helper into the WhatsApp
Web page, waits for the engine, lists the chats, and fetches the selected
transcript. Page-side tasks are asynchronous, so results are collected by
polling the helper's ``poll()`` function with a timer.
"""

import json
import logging
import os
import re

from gettext import gettext as _

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QDialog, QFileDialog, QProgressDialog

from zapzap.features.alerts.alert_manager import AlertManager
from zapzap.features.downloads.download_manager import DownloadManager
from zapzap.features.export_chat import media_writer
from zapzap.features.export_chat.formatter import format_rows
from zapzap.features.export_chat.ui.export_dialog import ExportChatDialog
from zapzap.features.export_chat.wa_js_provider import WaJsProvider


logger = logging.getLogger(__name__)

POLL_INTERVAL_MS = 500
ENGINE_READY_TIMEOUT_MS = 30_000
LIST_CHATS_TIMEOUT_MS = 60_000
# Fetching an entire history pulls older pages from the server.
EXPORT_TIMEOUT_MS = 600_000
MEDIA_DOWNLOAD_TIMEOUT_MS = 120_000


class ExportChatController(QObject):
    """Drive one export interaction from menu action to saved file."""

    finished = pyqtSignal()

    def __init__(self, page, parent_window):
        super().__init__(parent_window)
        self._page = page
        self._window = parent_window

        self._provider = WaJsProvider(self)
        self._provider.ready.connect(self._on_bundle_ready)
        self._provider.failed.connect(self._on_bundle_failed)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_tick)
        self._poll_handler = None
        self._poll_on_timeout = None
        self._poll_elapsed_ms = 0
        self._poll_timeout_ms = 0
        # runJavaScript callbacks can land after the phase that issued them
        # ended; a stale generation marks them as no-ops.
        self._poll_generation = 0

        self._progress = None
        self._done = False
        self._chat = None
        self._download_media = True
        self._rows = []
        self._txt_path = ""
        self._media_dir = ""
        self._media_files = {}
        self._media_queue = []
        self._media_total = 0

    # === Flow ===

    def start(self):
        self._show_progress(_("Preparing chat export…"))
        self._provider.ensure()

    def _on_bundle_failed(self, message):
        self._fail(_("Could not download the export engine (WA-JS): {}")
                   .format(message))

    def _on_bundle_ready(self, bundle_js):
        if self._done:
            return
        # The bundle defines window.WPP; skip re-evaluation on reuse.
        self._page.runJavaScript(
            "if (typeof window.WPP === 'undefined') {\n"
            + bundle_js +
            "\n}"
        )
        self._page.runJavaScript(self._helper_source())
        self._wait_engine_ready()

    def _wait_engine_ready(self):
        self._set_progress_text(_("Waiting for WhatsApp Web…"))
        generation = self._start_polling_generation()

        def on_ready_tick():
            self._page.runJavaScript(
                "!!(window._zapzapExport && "
                "window._zapzapExport.isEngineReady())",
                lambda ready: self._on_engine_ready_check(ready, generation),
            )

        self._start_polling(on_ready_tick, ENGINE_READY_TIMEOUT_MS)

    def _on_engine_ready_check(self, ready, generation):
        if self._done or generation != self._poll_generation:
            return
        if not ready:
            return  # keep polling until the timeout
        self._stop_polling()
        self._list_chats()

    def _list_chats(self):
        self._set_progress_text(_("Loading chats…"))
        self._page.runJavaScript("window._zapzapExport.listChats()")
        self._start_task_polling(self._on_chats_listed, LIST_CHATS_TIMEOUT_MS)

    def _on_chats_listed(self, chats):
        self._close_progress()
        if not chats:
            self._fail(_("No chats were found on this account."))
            return

        dialog = ExportChatDialog(chats, self._window)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        chat = dialog.selected_chat
        count = dialog.selected_count
        download_media = dialog.download_media
        dialog.deleteLater()

        if not accepted or chat is None:
            self._finish()
            return

        self._chat = chat
        self._download_media = download_media
        self._show_progress(
            _("Exporting “{}”…").format(chat["name"]))
        script = "window._zapzapExport.exportMessages({}, {}, {})".format(
            json.dumps(chat["id"]),
            int(count),
            json.dumps(_("You")),
        )
        self._page.runJavaScript(script)
        self._start_task_polling(self._on_messages_exported, EXPORT_TIMEOUT_MS)

    def _on_messages_exported(self, rows):
        self._close_progress()
        self._rows = rows or []

        chat_name = self._chat["name"] if self._chat else _("chat")
        safe_name = re.sub(r"[\\/:*?\"<>|]+", "_", chat_name).strip() or "chat"
        default_path = os.path.join(
            DownloadManager.get_path(),
            _("WhatsApp Chat with {}").format(safe_name) + ".txt",
        )

        path, _selected_filter = QFileDialog.getSaveFileName(
            self._window,
            _("Save chat export"),
            default_path,
            _("Text files (*.txt)"),
        )
        if not path:
            self._finish()
            return

        self._txt_path = path
        self._media_dir = os.path.splitext(path)[0] + "_media"
        self._media_files = {}

        if self._download_media:
            self._media_queue = [
                (index, row)
                for index, row in enumerate(self._rows, start=1)
                if media_writer.is_downloadable(row) and row.get("id")
            ]
        else:
            self._media_queue = []
        self._media_total = len(self._media_queue)

        if self._media_queue:
            self._show_progress(_("Downloading media…"))
            self._download_next_media()
        else:
            self._write_transcript()

    def _download_next_media(self):
        if self._done:
            return
        if not self._media_queue:
            self._write_transcript()
            return

        index, row = self._media_queue.pop(0)
        done = self._media_total - len(self._media_queue)
        self._set_progress_text(
            _("Downloading media {done} of {total}…").format(
                done=done, total=self._media_total))

        self._page.runJavaScript(
            "window._zapzapExport.downloadMedia({})".format(
                json.dumps(row["id"])))
        self._start_task_polling(
            lambda data_url: self._on_media_downloaded(data_url, index, row),
            MEDIA_DOWNLOAD_TIMEOUT_MS,
            on_error=lambda message: self._on_media_failed(message, row),
        )

    def _on_media_downloaded(self, data_url, index, row):
        if data_url:
            try:
                filename = media_writer.build_filename(row, index)
                media_writer.write_media(
                    self._media_dir, filename, data_url)
                self._media_files[row["id"]] = filename
            except (OSError, ValueError):
                # A single unwritable attachment falls back to a placeholder.
                logger.exception("Failed to save exported media")
        self._download_next_media()

    def _on_media_failed(self, message, row):
        # WhatsApp Web can refuse a specific media; keep the placeholder.
        logger.warning("Media download failed for %s: %s",
                       row.get("id"), message)
        self._download_next_media()

    def _write_transcript(self):
        self._close_progress()
        transcript = format_rows(self._rows, self._media_files)
        try:
            with open(self._txt_path, "w", encoding="utf-8") as output:
                output.write(transcript)
        except OSError as error:
            self._fail(_("Could not write the file: {}").format(error))
            return

        if self._media_files:
            message = _("Chat exported to {}\nMedia saved to {}").format(
                self._txt_path, self._media_dir)
        else:
            message = _("Chat exported to {}").format(self._txt_path)
        AlertManager.information(self._window, _("Export chat"), message)
        self._finish()

    # === Page task polling ===

    def _start_task_polling(self, on_result, timeout_ms, on_error=None):
        generation = self._start_polling_generation()

        def on_task_tick():
            self._page.runJavaScript(
                "window._zapzapExport ? window._zapzapExport.poll() : null",
                lambda outcome: self._on_task_poll(
                    outcome, on_result, on_error, generation),
            )

        self._start_polling(on_task_tick, timeout_ms, on_timeout=on_error)

    def _on_task_poll(self, outcome, on_result, on_error, generation):
        if self._done or generation != self._poll_generation:
            return
        if outcome is None:
            self._fail(_("The export helper is not available on the page."))
            return

        status = outcome.get("status")
        if status == "done":
            self._stop_polling()
            on_result(outcome.get("result"))
        elif status == "error":
            self._stop_polling()
            message = outcome.get("error")
            if on_error is not None:
                on_error(message)
            else:
                self._fail(_("WhatsApp Web reported an error: {}")
                           .format(message))
        # 'working'/'idle' keep polling until the timeout

    def _start_polling_generation(self) -> int:
        self._poll_generation += 1
        return self._poll_generation

    def _start_polling(self, handler, timeout_ms, on_timeout=None):
        self._poll_handler = handler
        self._poll_on_timeout = on_timeout
        self._poll_elapsed_ms = 0
        self._poll_timeout_ms = timeout_ms
        self._poll_timer.start()

    def _poll_tick(self):
        self._poll_elapsed_ms += POLL_INTERVAL_MS
        if self._poll_elapsed_ms > self._poll_timeout_ms:
            on_timeout = self._poll_on_timeout
            self._stop_polling()
            message = _("The operation timed out.")
            # A per-task timeout handler keeps one stuck item from aborting
            # the whole export (used by media downloads).
            if on_timeout is not None:
                on_timeout(message)
            else:
                self._fail(message)
            return
        if self._poll_handler is not None:
            self._poll_handler()

    def _stop_polling(self):
        self._poll_timer.stop()
        self._poll_handler = None
        self._poll_on_timeout = None

    # === Progress and termination ===

    def _show_progress(self, text):
        if self._progress is None:
            self._progress = QProgressDialog(
                text, _("Cancel"), 0, 0, self._window)
            self._progress.setWindowTitle(_("Export chat"))
            self._progress.setMinimumDuration(0)
            self._progress.canceled.connect(self._on_canceled)
            self._progress.setAutoClose(False)
            self._progress.setAutoReset(False)
        else:
            self._progress.setLabelText(text)
        self._progress.show()

    def _set_progress_text(self, text):
        if self._progress is not None:
            self._progress.setLabelText(text)

    def _close_progress(self):
        if self._progress is not None:
            progress = self._progress
            self._progress = None
            progress.canceled.disconnect(self._on_canceled)
            progress.close()
            progress.deleteLater()

    def _on_canceled(self):
        self._provider.abort()
        self._finish()

    def _fail(self, message):
        if self._done:
            return
        self._close_progress()
        AlertManager.warning(self._window, _("Export chat"), message)
        self._finish()

    def _finish(self):
        if self._done:
            return
        self._done = True
        self._stop_polling()
        self._close_progress()
        self.finished.emit()

    # === Helpers ===

    @staticmethod
    def _helper_source() -> str:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        js_path = os.path.join(base_dir, "scripts", "export_chat.js")
        with open(js_path, "r", encoding="utf-8") as helper:
            return helper.read()
