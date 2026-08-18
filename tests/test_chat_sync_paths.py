# tests/test_chat_sync_paths.py
import unittest

from zapzap.features.chat_sync import paths


class PathsTests(unittest.TestCase):
    def test_db_path_under_media_dir_parent(self):
        # Both live under the same app-local base directory.
        import os
        self.assertEqual(
            os.path.dirname(paths.db_path()),
            os.path.dirname(paths.media_dir()),
        )
        self.assertTrue(paths.db_path().endswith("messages.db"))
        self.assertTrue(paths.media_dir().endswith("chat_media"))


if __name__ == "__main__":
    unittest.main()
