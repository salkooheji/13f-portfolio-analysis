"""Applies cover page parsing to all filings and records the results.

Also links each amendment to the filing it supersedes: the original
13F-HR from the same manager for the same period. Nothing is deleted
or overwritten; supersedes is a pointer, which is what preserves the
point-in-time view.
"""

import logging
from pathlib import Path

from edgar13f.parse.coverpage import parse_cover

logger = logging.getLogger(__name__)


def apply_cover_metadata(conn) -> int:
    """Parse cover pages for all filings; returns count of failures."""
    failures = 0
    filings = conn.execute(
        "SELECT accession_no, raw_path, period_of_report FROM filings"
    ).fetchall()

    for filing in filings:
        raw = Path(filing["raw_path"]).read_bytes()
        try:
            cover = parse_cover(raw)
        except Exception as exc:
            logger.error("Cover parse failed for %s: %s", filing["accession_no"], exc)
            failures += 1
            continue

        if cover["period_of_report"] != filing["period_of_report"]:
            logger.warning(
                "%s: cover page period %s disagrees with index %s; "
                "trusting the cover page",
                filing["accession_no"],
                cover["period_of_report"],
                filing["period_of_report"],
            )

        conn.execute(
            """
            UPDATE filings
            SET period_of_report = ?, amendment_type = ?, is_ct_request = ?
            WHERE accession_no = ?
            """,
            (
                cover["period_of_report"],
                cover["amendment_type"],
                int(cover["is_ct_request"]),
                filing["accession_no"],
            ),
        )
    conn.commit()

    # Link amendments to what they amend: the original 13F-HR of the
    # same manager and period.
    conn.execute(
        """
        UPDATE filings AS amendment
        SET supersedes = (
            SELECT original.accession_no FROM filings AS original
            WHERE original.cik = amendment.cik
              AND original.period_of_report = amendment.period_of_report
              AND original.form_type = '13F-HR'
        )
        WHERE amendment.form_type = '13F-HR/A'
        """
    )
    conn.commit()
    return failures
