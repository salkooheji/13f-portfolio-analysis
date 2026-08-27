"""Per-filing verification of the value column's units.

13F values were reported in thousands of dollars for decades; the
EDGAR technical specification switched to whole dollars for filings
submitted from early 2023. Rather than hard-coding that boundary, we
verify each filing against its own data: raw_value divided by shares
is an implied share price, and only the correct unit hypothesis puts
the filing's median implied price into a plausible range.

A filing that fails verification keeps value_unit NULL and value_usd
NULL, which excludes it loudly from all valuation downstream. We do
not guess."""

import logging
import statistics

logger = logging.getLogger(__name__)

# A median implied share price is accepted as plausible in this range.
# The range must span LESS than the 1000x gap between the two unit
# hypotheses, otherwise a median in the overlap zone satisfies both
# and nothing can ever be decided there. [2, 2000] spans 1000x at
# most at its edges, so the hypotheses are mutually exclusive for
# any median, while still covering realistic book-median prices.
PLAUSIBLE_MEDIAN_LOW = 2.0
PLAUSIBLE_MEDIAN_HIGH = 2_000.0


def infer_value_unit(conn, accession_no: str) -> str | None:
    """Return 'DOLLARS', 'THOUSANDS', or None if undecidable."""
    rows = conn.execute(
        """
        SELECT raw_value, shares FROM holdings
        WHERE accession_no = ? AND share_type = 'SH' AND shares > 0
        """,
        (accession_no,),
    ).fetchall()
    if not rows:
        return None  # nothing to infer from, e.g. all-PRN filings

    median_implied = statistics.median(
        row["raw_value"] / row["shares"] for row in rows
    )

    dollars_ok = PLAUSIBLE_MEDIAN_LOW <= median_implied <= PLAUSIBLE_MEDIAN_HIGH
    thousands_ok = (
        PLAUSIBLE_MEDIAN_LOW <= median_implied * 1000 <= PLAUSIBLE_MEDIAN_HIGH
    )

    if dollars_ok and not thousands_ok:
        return "DOLLARS"
    if thousands_ok and not dollars_ok:
        return "THOUSANDS"
    logger.warning(
        "%s: units undecidable, median implied price %.4f",
        accession_no,
        median_implied,
    )
    return None


def apply_unit_verification(conn) -> tuple[int, int]:
    """Verify units for all unverified filings and fill value_usd.

    Returns (verified, undecided). Idempotent: filings with a verdict
    are skipped, and recomputing would produce the same verdict anyway
    since the inputs are immutable.
    """
    verified, undecided = 0, 0
    pending = conn.execute(
        "SELECT accession_no FROM filings "
        "WHERE parse_status = 'parsed' AND value_unit IS NULL"
    ).fetchall()

    for filing in pending:
        accession_no = filing["accession_no"]
        unit = infer_value_unit(conn, accession_no)
        if unit is None:
            undecided += 1
            continue
        factor = 1000 if unit == "THOUSANDS" else 1
        conn.execute("BEGIN")
        conn.execute(
            "UPDATE filings SET value_unit = ? WHERE accession_no = ?",
            (unit, accession_no),
        )
        conn.execute(
            "UPDATE holdings SET value_usd = raw_value * ? WHERE accession_no = ?",
            (factor, accession_no),
        )
        conn.commit()
        verified += 1
        logger.info("%s: value unit %s", accession_no, unit)
    return verified, undecided
