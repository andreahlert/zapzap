# zapzap/features/chat_sync/paths.py
"""Resolve the sync store paths under the application data directory."""

import os

from PyQt6.QtCore import QStandardPaths


def _base() -> str:
    return QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation)


def db_path() -> str:
    return os.path.join(_base(), "messages.db")


def media_dir() -> str:
    return os.path.join(_base(), "chat_media")
