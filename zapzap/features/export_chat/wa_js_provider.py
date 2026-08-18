"""Resolve the WA-JS bundle used by the chat export feature.

The bundle is downloaded once from the official WPPConnect releases and cached
under the application data directory. Delete the cached file to force a
re-download with a newer WA-JS build.
"""

import logging
import os

from PyQt6.QtCore import QObject, QStandardPaths, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


logger = logging.getLogger(__name__)

WA_JS_URL = (
    "https://github.com/wppconnect-team/wa-js/releases/latest/download/"
    "wppconnect-wa.js"
)


class WaJsProvider(QObject):
    """Provide the wppconnect-wa.js source from the local cache or GitHub."""

    ready = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._reply = None

    @staticmethod
    def cache_path() -> str:
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppLocalDataLocation
        )
        return os.path.join(base, "wa-js", "wppconnect-wa.js")

    def ensure(self):
        """Emit ``ready`` with the bundle source, downloading it if needed."""
        path = self.cache_path()
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as bundle:
                    self.ready.emit(bundle.read())
                return
            except OSError:
                logger.exception("Failed to read the cached WA-JS bundle")

        request = QNetworkRequest(QUrl(WA_JS_URL))
        self._reply = self._manager.get(request)
        self._reply.finished.connect(self._on_download_finished)

    def abort(self):
        if self._reply is not None:
            reply = self._reply
            self._reply = None
            reply.abort()
            reply.deleteLater()

    def _on_download_finished(self):
        reply = self._reply
        self._reply = None
        if reply is None:
            return
        reply.deleteLater()

        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.failed.emit(reply.errorString())
            return

        code = bytes(reply.readAll()).decode("utf-8", errors="replace")

        path = self.cache_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as bundle:
                bundle.write(code)
        except OSError:
            # A cache write failure only costs a re-download next time.
            logger.exception("Failed to cache the WA-JS bundle")

        self.ready.emit(code)
