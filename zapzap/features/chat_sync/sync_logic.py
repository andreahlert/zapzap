"""Pure cursor and paging helpers for the sync engine (no Qt, no I/O)."""


def select_new_rows(rows, last_ts, last_id):
    """Split a newest-first page against the stored cursor.

    Returns (new_rows, reached_cursor). On first backfill (last_ts is None)
    every row is new and the cursor is never "reached".
    """
    if last_ts is None:
        return list(rows), False

    new_rows = []
    reached = False
    for row in rows:
        ts = row.get("ts") or 0
        if row.get("id") == last_id or ts <= last_ts:
            reached = True
            break
        new_rows.append(row)
    return new_rows, reached


def next_cursor(rows, previous):
    """Return the newest (ts, id) from a newest-first page, else previous."""
    if not rows:
        return previous
    newest = rows[0]
    return (newest.get("ts") or 0, newest.get("id"))
