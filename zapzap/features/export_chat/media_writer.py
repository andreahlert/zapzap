"""Decode WA-JS media payloads and write them next to the transcript."""

import base64
import mimetypes
import os
import re


# Message types whose media WA-JS can decrypt and download.
DOWNLOADABLE_TYPES = {
    "image",
    "video",
    "audio",
    "ptt",
    "sticker",
    "document",
    "gif",
}

# Type prefixes for generated attachment names, mirroring the mobile export.
_TYPE_PREFIX = {
    "image": "IMG",
    "video": "VID",
    "audio": "AUD",
    "ptt": "PTT",
    "sticker": "STK",
    "document": "DOC",
    "gif": "GIF",
}

# Extensions preferred over mimetypes.guess_extension for common WhatsApp media.
_EXTENSION_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "application/pdf": ".pdf",
}

_DATA_URL_PREFIX = re.compile(r"^data:[^;,]*;base64,", re.IGNORECASE)


def is_downloadable(row) -> bool:
    return row.get("type") in DOWNLOADABLE_TYPES


def _sanitize(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name).strip()
    return name or "file"


def _extension(mimetype: str, fallback: str = "") -> str:
    mimetype = (mimetype or "").split(";")[0].strip().lower()
    if mimetype in _EXTENSION_BY_MIME:
        return _EXTENSION_BY_MIME[mimetype]
    guessed = mimetypes.guess_extension(mimetype) if mimetype else None
    return guessed or fallback


def guess_extension(mimetype: str, fallback: str = ".bin") -> str:
    """Public helper: file extension for a mimetype, with a fallback."""
    return _extension(mimetype, fallback)


def build_filename(row, index: int) -> str:
    """Build a unique, safe attachment file name for a media row."""
    original = row.get("filename", "")
    if original:
        base = _sanitize(original)
        stem, ext = os.path.splitext(base)
        if not ext:
            ext = _extension(row.get("mimetype", ""), ".bin")
        return f"{index:05d}-{stem}{ext}"

    prefix = _TYPE_PREFIX.get(row.get("type"), "FILE")
    ext = _extension(row.get("mimetype", ""), ".bin")
    return f"{index:05d}-{prefix}{ext}"


def decode_data_url(data_url: str) -> bytes:
    """Decode a ``data:...;base64,`` payload into raw bytes."""
    payload = _DATA_URL_PREFIX.sub("", data_url or "")
    return base64.b64decode(payload, validate=False)


def write_media(directory: str, filename: str, data_url: str) -> None:
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, filename), "wb") as output:
        output.write(decode_data_url(data_url))
