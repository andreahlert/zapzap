"""Render exported message rows in the WhatsApp mobile export layout."""

from datetime import datetime
from gettext import gettext as _


# System rows that the mobile export also omits from the transcript.
SKIPPED_TYPES = {
    "e2e_notification",
    "gp2",
    "notification_template",
    "protocol",
}


def _media_placeholder(row) -> str:
    placeholders = {
        "revoked": _("This message was deleted"),
        "call_log": _("Call"),
        "location": _("Location shared"),
        "vcard": _("Contact card"),
        "multi_vcard": _("Contact card"),
    }
    return placeholders.get(row.get("type"), _("<Media omitted>"))


def format_rows(rows, media_files=None) -> str:
    """Format rows as ``DD/MM/YYYY HH:MM - Sender: message`` lines.

    ``media_files`` optionally maps a message id to the saved attachment file
    name. Media rows with a saved file are rendered as ``<name> (file
    attached)``; the rest keep the ``<Media omitted>`` placeholder.
    """
    media_files = media_files or {}
    lines = []
    for row in rows:
        message_type = row.get("type", "chat")
        if message_type in SKIPPED_TYPES:
            continue

        if message_type == "chat":
            text = row.get("body", "")
            if not text:
                continue
        else:
            attachment = media_files.get(row.get("id"))
            if attachment:
                text = _("{} (file attached)").format(attachment)
            else:
                text = _media_placeholder(row)
            caption = row.get("caption", "")
            if caption:
                text = f"{text} {caption}"

        timestamp = row.get("t", 0)
        if not timestamp:
            continue

        stamp = datetime.fromtimestamp(timestamp).strftime("%d/%m/%Y %H:%M")
        sender = row.get("sender", "")
        lines.append(f"{stamp} - {sender}: {text}")

    return "\n".join(lines) + "\n"
