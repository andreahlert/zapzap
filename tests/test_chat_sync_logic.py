import unittest

from zapzap.features.chat_sync.sync_logic import select_new_rows, next_cursor


def rows(*pairs):
    return [{"id": i, "ts": t} for (t, i) in pairs]


class SelectNewRowsTests(unittest.TestCase):
    def test_first_backfill_takes_all(self):
        new, reached = select_new_rows(rows((3, "c"), (2, "b"), (1, "a")),
                                       None, None)
        self.assertEqual([r["id"] for r in new], ["c", "b", "a"])
        self.assertFalse(reached)

    def test_stops_at_cursor_ts(self):
        new, reached = select_new_rows(rows((3, "c"), (2, "b"), (1, "a")),
                                       2, "b")
        self.assertEqual([r["id"] for r in new], ["c"])
        self.assertTrue(reached)

    def test_reached_by_id_even_if_ts_equal(self):
        new, reached = select_new_rows(rows((2, "b2"), (2, "b")), 2, "b")
        self.assertEqual([r["id"] for r in new], [])
        self.assertTrue(reached)


class NextCursorTests(unittest.TestCase):
    def test_takes_newest(self):
        self.assertEqual(
            next_cursor(rows((3, "c"), (2, "b")), (0, None)), (3, "c"))

    def test_empty_keeps_previous(self):
        self.assertEqual(next_cursor([], (5, "x")), (5, "x"))


if __name__ == "__main__":
    unittest.main()
