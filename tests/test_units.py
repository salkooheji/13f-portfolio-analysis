"""Unit verification against an in-memory database.

Covers both regimes and the refusal case: the range spans less than
the 1000x hypothesis gap, so no median can satisfy both."""

import pytest

from edgar13f.db import get_connection, init_db
from edgar13f.normalize.units import infer_value_unit


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "test.db"))
    init_db(connection)
    connection.execute(
        "INSERT INTO filings (accession_no, cik, form_type, period_of_report,"
        " filed_date, raw_path, parse_status, ingested_at)"
        " VALUES ('ACC-1', 1, '13F-HR', '2024-06-30', '2024-08-01', 'x',"
        " 'parsed', 'now')"
    )
    return connection


def holdings_row(conn, value, shares, row_index):
    conn.execute(
        "INSERT INTO holdings (accession_no, row_index, issuer_name, cusip,"
        " raw_value, shares, share_type) VALUES ('ACC-1', ?, 'TEST', 'C', ?,"
        " ?, 'SH')",
        (row_index, value, shares),
    )


def test_dollars_era_detected(conn):
    # Implied prices around $150: plausible as dollars only.
    holdings_row(conn, 150_000_000, 1_000_000, 0)
    holdings_row(conn, 75_000_000, 500_000, 1)
    assert infer_value_unit(conn, "ACC-1") == "DOLLARS"


def test_thousands_era_detected(conn):
    # Same book as reported in thousands: implied 0.15, only x1000 works.
    holdings_row(conn, 150_000, 1_000_000, 0)
    holdings_row(conn, 75_000, 500_000, 1)
    assert infer_value_unit(conn, "ACC-1") == "THOUSANDS"


def test_ambiguous_book_refused(conn):
    # Median implied exactly 2.0 satisfies neither range strictly
    # on one side: engineered so both hypotheses fail.
    holdings_row(conn, 100_000, 1, 0)  # implied 100,000: too high for
    # dollars, and x1000 far too high for thousands.
    assert infer_value_unit(conn, "ACC-1") is None
