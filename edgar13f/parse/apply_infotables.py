"""Parses information tables for all pending filings.

Idempotent by status: only filings with parse_status='pending' are
processed, so reruns touch nothing already parsed. Each filing is
processed in a transaction: either all its holdings land and the
status becomes 'parsed', or none do and the status records the error.
"""

import logging
from pathlib import Path

from edgar13f.parse.infotable import parse_infotable

logger = logging.getLogger(__name__)


def apply_infotables(conn) -> tuple[int, int]:
    """Parse all pending filings. Returns (parsed, failed)."""
    parsed, failed = 0, 0
    pending = conn.execute(
        "SELECT accession_no, raw_path FROM filings WHERE parse_status = 'pending'"
    ).fetchall()

    for filing in pending:
        raw = Path(filing["raw_path"]).read_bytes()
        try:
            rows = parse_infotable(raw)
            conn.execute("BEGIN")
            conn.executemany(
                """
                INSERT INTO holdings
                    (accession_no, row_index, issuer_name, class_title,
                     cusip, raw_value, shares, share_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        filing["accession_no"],
                        row["row_index"],
                        row["issuer_name"],
                        row["class_title"],
                        row["cusip"],
                        row["raw_value"],
                        row["shares"],
                        row["share_type"],
                    )
                    for row in rows
                ],
            )
            conn.execute(
                "UPDATE filings SET parse_status='parsed' WHERE accession_no=?",
                (filing["accession_no"],),
            )
            conn.commit()
            parsed += 1
            logger.info("Parsed %s: %d holdings", filing["accession_no"], len(rows))
        except Exception as exc:
            conn.rollback()
            conn.execute(
                "UPDATE filings SET parse_status='failed', parse_error=? "
                "WHERE accession_no=?",
                (str(exc), filing["accession_no"]),
            )
            conn.commit()
            failed += 1
            logger.error("Parse failed for %s: %s", filing["accession_no"], exc)
    return parsed, failed


def parse_coverage(conn) -> dict:
    """The audit number: filings parsed over filings total."""
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN parse_status = 'parsed' THEN 1 ELSE 0 END) AS parsed,
            SUM(CASE WHEN parse_status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM filings
        """
    ).fetchone()
    total, parsed_n = row["total"], row["parsed"] or 0
    return {
        "total": total,
        "parsed": parsed_n,
        "failed": row["failed"] or 0,
        "coverage_pct": round(100.0 * parsed_n / total, 2) if total else 0.0,
    }
