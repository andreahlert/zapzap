"""Tests for the chat export transcript formatter."""

import unittest

from zapzap.features.export_chat.formatter import format_rows


# 2021-01-01 00:00:00 UTC. The formatter renders in local time, so the tests
# assert on structure and payload rather than on a fixed clock reading.
BASE_TS = 1609459200


class FormatRowsTests(unittest.TestCase):
    def test_text_message_uses_sender_and_body(self):
        output = format_rows([
            {"t": BASE_TS, "sender": "Alice", "type": "chat", "body": "Hi"},
        ])
        self.assertTrue(output.endswith("\n"))
        line = output.strip()
        self.assertRegex(line, r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2} - Alice: Hi$")

    def test_media_message_uses_placeholder_with_caption(self):
        output = format_rows([
            {"t": BASE_TS, "sender": "Bob", "type": "image",
             "body": "", "caption": "beach"},
        ]).strip()
        self.assertIn(": <Media omitted> beach", output)

    def test_empty_text_and_system_rows_are_skipped(self):
        output = format_rows([
            {"t": BASE_TS, "sender": "Alice", "type": "chat", "body": ""},
            {"t": BASE_TS, "sender": "", "type": "e2e_notification", "body": ""},
            {"t": 0, "sender": "Alice", "type": "chat", "body": "no stamp"},
        ])
        self.assertEqual(output, "\n")

    def test_deleted_message_placeholder(self):
        output = format_rows([
            {"t": BASE_TS, "sender": "Alice", "type": "revoked", "body": ""},
        ]).strip()
        self.assertIn(": This message was deleted", output)

    def test_rows_keep_input_order(self):
        output = format_rows([
            {"t": BASE_TS, "sender": "Alice", "type": "chat", "body": "first"},
            {"t": BASE_TS + 60, "sender": "Bob", "type": "chat",
             "body": "second"},
        ])
        lines = output.strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith(": first"))
        self.assertTrue(lines[1].endswith(": second"))


if __name__ == "__main__":
    unittest.main()
