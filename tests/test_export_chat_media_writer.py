"""Tests for the chat export media writer helpers."""

import base64
import os
import tempfile
import unittest

from zapzap.features.export_chat import media_writer
from zapzap.features.export_chat.formatter import format_rows


PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-body"
BASE_TS = 1609459200


class MediaWriterTests(unittest.TestCase):
    def test_is_downloadable_covers_media_types(self):
        self.assertTrue(media_writer.is_downloadable({"type": "image"}))
        self.assertTrue(media_writer.is_downloadable({"type": "ptt"}))
        self.assertFalse(media_writer.is_downloadable({"type": "chat"}))
        self.assertFalse(media_writer.is_downloadable({"type": "location"}))

    def test_build_filename_uses_type_prefix_and_mime_extension(self):
        name = media_writer.build_filename(
            {"type": "image", "mimetype": "image/jpeg"}, 7)
        self.assertEqual(name, "00007-IMG.jpg")

    def test_build_filename_keeps_document_name_and_index(self):
        name = media_writer.build_filename(
            {"type": "document", "filename": "report.pdf",
             "mimetype": "application/pdf"}, 3)
        self.assertEqual(name, "00003-report.pdf")

    def test_build_filename_sanitizes_and_falls_back_extension(self):
        name = media_writer.build_filename(
            {"type": "document", "filename": "a/b:c", "mimetype": ""}, 1)
        self.assertEqual(name, "00001-a_b_c.bin")

    def test_decode_data_url_strips_prefix(self):
        payload = base64.b64encode(PNG_BYTES).decode()
        decoded = media_writer.decode_data_url(
            f"data:image/png;base64,{payload}")
        self.assertEqual(decoded, PNG_BYTES)

    def test_write_media_creates_directory_and_file(self):
        payload = base64.b64encode(PNG_BYTES).decode()
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = os.path.join(tmp, "chat_media")
            media_writer.write_media(
                media_dir, "00001-IMG.png",
                f"data:image/png;base64,{payload}")
            written = os.path.join(media_dir, "00001-IMG.png")
            self.assertTrue(os.path.isfile(written))
            with open(written, "rb") as saved:
                self.assertEqual(saved.read(), PNG_BYTES)

    def test_formatter_references_saved_attachment(self):
        rows = [
            {"id": "m1", "t": BASE_TS, "sender": "Alice", "type": "image",
             "body": "", "caption": "beach"},
        ]
        output = format_rows(rows, {"m1": "00001-IMG.jpg"}).strip()
        self.assertIn(": 00001-IMG.jpg (file attached) beach", output)

    def test_formatter_keeps_placeholder_without_attachment(self):
        rows = [
            {"id": "m1", "t": BASE_TS, "sender": "Alice", "type": "image",
             "body": "", "caption": ""},
        ]
        output = format_rows(rows, {}).strip()
        self.assertIn(": <Media omitted>", output)

    def test_guess_extension_public_helper(self):
        self.assertEqual(media_writer.guess_extension("image/jpeg"), ".jpg")
        self.assertEqual(media_writer.guess_extension("", ".dat"), ".dat")


if __name__ == "__main__":
    unittest.main()
