"""The audit's trap, as an executable proof: a CUSIP mapping failure
must not appear as a trade, and the amendment policy must compose
quarters correctly."""

import pytest

from edgar13f.analysis.diffs import diff_quarters, quarter_filings
from edgar13f.db import get_connection, init_db


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "test.db"))
    init_db(connection)
    return connection


def add_filing(conn, accession, period, form="13F-HR", amendment=None,
               filed="2024-01-01"):
    conn.execute(
        "INSERT INTO filings (accession_no, cik, form_type,"
        " period_of_report, filed_date, amendment_type, raw_path,"
        " parse_status, ingested_at) VALUES (?, 1, ?, ?, ?, ?, 'x',"
        " 'parsed', 'now')",
        (accession, form, period, filed, amendment),
    )


def add_holding(conn, accession, cusip, shares, value, row_index=0):
    conn.execute(
        "INSERT INTO holdings (accession_no, row_index, issuer_name,"
        " cusip, raw_value, value_usd, shares, share_type)"
        " VALUES (?, ?, 'TEST', ?, ?, ?, ?, 'SH')",
        (accession, row_index, cusip, value, value, shares),
    )


def test_unmapped_cusip_is_not_a_trade(conn):
    """A CUSIP held identically in both quarters, absent from
    cusip_map entirely: it must diff as UNCHANGED with its mapping
    status visible, never as an open or exit."""
    add_filing(conn, "A1", "2024-03-31")
    add_filing(conn, "A2", "2024-06-30")
    add_holding(conn, "A1", "UNMAPPED99", 1000, 50_000)
    add_holding(conn, "A2", "UNMAPPED99", 1000, 55_000)

    diff = diff_quarters(conn, 1, "2024-03-31", "2024-06-30")
    row = diff.loc["UNMAPPED99"]
    assert row["change_type"] == "UNCHANGED"
    assert row["mapping_status"] == "unmapped"


def test_value_move_without_share_change_is_unchanged(conn):
    """Price moved 20%, shares did not: not a trade."""
    add_filing(conn, "B1", "2024-03-31")
    add_filing(conn, "B2", "2024-06-30")
    add_holding(conn, "B1", "CUSIP0001", 500, 100_000)
    add_holding(conn, "B2", "CUSIP0001", 500, 120_000)

    diff = diff_quarters(conn, 1, "2024-03-31", "2024-06-30")
    assert diff.loc["CUSIP0001", "change_type"] == "UNCHANGED"


def test_restatement_replaces_original(conn):
    add_filing(conn, "C1", "2024-03-31", filed="2024-05-01")
    add_filing(conn, "C2", "2024-03-31", form="13F-HR/A",
               amendment="RESTATEMENT", filed="2024-06-01")
    assert quarter_filings(conn, 1, "2024-03-31") == ["C2"]


def test_new_holdings_adds_to_original(conn):
    add_filing(conn, "D1", "2024-03-31", filed="2024-05-01")
    add_filing(conn, "D2", "2024-03-31", form="13F-HR/A",
               amendment="NEW HOLDINGS", filed="2024-06-01")
    selected = quarter_filings(conn, 1, "2024-03-31")
    assert selected == ["D1", "D2"]


def test_sub_account_rows_summed_per_cusip(conn):
    """One CUSIP across multiple rows must aggregate before diffing,
    like Berkshire's five Activision rows."""
    add_filing(conn, "E1", "2024-03-31")
    add_filing(conn, "E2", "2024-06-30")
    for i, shares in enumerate([100, 200, 300]):
        add_holding(conn, "E1", "SPLITROWS1", shares, 1_000, row_index=i)
    add_holding(conn, "E2", "SPLITROWS1", 600, 3_000)

    diff = diff_quarters(conn, 1, "2024-03-31", "2024-06-30")
    row = diff.loc["SPLITROWS1"]
    assert row["shares_prev"] == 600
    assert row["change_type"] == "UNCHANGED"
