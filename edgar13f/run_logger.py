"""Records one row per pipeline run in the run_log table.

The audit inspects exactly these fields: filings fetched, filings
skipped, parse failures, runtime. Context-manager form guarantees
the row is finalized even when a stage raises."""

import datetime
import time


class RunLogger:
    def __init__(self, conn, triggered_by: str):
        self._conn = conn
        self._triggered_by = triggered_by
        self._start_monotonic = None
        self.run_id = None
        self.filings_fetched = 0
        self.filings_skipped = 0
        self.parse_failures = 0

    def __enter__(self):
        self._start_monotonic = time.monotonic()
        cursor = self._conn.execute(
            "INSERT INTO run_log (started_at, triggered_by) VALUES (?, ?)",
            (
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
                self._triggered_by,
            ),
        )
        self._conn.commit()
        self.run_id = cursor.lastrowid
        return self

    def __exit__(self, exc_type, exc, tb):
        self._conn.execute(
            """
            UPDATE run_log
            SET finished_at = ?, filings_fetched = ?, filings_skipped = ?,
                parse_failures = ?, runtime_seconds = ?
            WHERE run_id = ?
            """,
            (
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
                self.filings_fetched,
                self.filings_skipped,
                self.parse_failures,
                round(time.monotonic() - self._start_monotonic, 2),
                self.run_id,
            ),
        )
        self._conn.commit()
        return False  # never swallow exceptions
    