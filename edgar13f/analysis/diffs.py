"""Quarter-over-quarter position changes for one manager.

Correctness rules, in order of importance:
  1. Diffs are computed on CUSIPs as reported in filings. Ticker
     mapping is joined on afterward for display only, so a CUSIP
     that fails to map can never appear as a trade.
  2. Changes are detected on share counts, not values. Values move
     with prices even when nothing was traded.
  3. Holdings are summed by CUSIP within a quarter first, because
     filers report positions split across multiple rows.
  4. Which filings constitute a quarter is resolved by one shared
     function implementing the amendment policy: latest RESTATEMENT
     replaces the original, NEW HOLDINGS amendments add on top.
"""

import pandas as pd


def quarter_filings(conn, cik: int, period: str) -> list[str]:
    """Accession numbers whose holdings together form the current
    view of this manager-quarter, per the amendment policy."""
    rows = conn.execute(
        """
        SELECT accession_no, form_type, amendment_type
        FROM filings
        WHERE cik = ? AND period_of_report = ? AND parse_status = 'parsed'
        ORDER BY filed_date DESC, accession_no DESC
        """,
        (cik, period),
    ).fetchall()
    if not rows:
        return []

    restatements = [
        r for r in rows if (r["amendment_type"] or "") == "RESTATEMENT"
    ]
    originals = [r for r in rows if r["form_type"] == "13F-HR"]
    new_holdings = [
        r for r in rows if (r["amendment_type"] or "") == "NEW HOLDINGS"
    ]

    base = restatements[0] if restatements else (
        originals[0] if originals else None
    )
    if base is None:
        return []
    selected = [base["accession_no"]]
    selected += [r["accession_no"] for r in new_holdings]
    return selected


def quarter_positions(conn, cik: int, period: str) -> pd.DataFrame:
    """One row per CUSIP for the quarter: shares and value summed
    across constituent filings and sub-account rows."""
    accessions = quarter_filings(conn, cik, period)
    if not accessions:
        return pd.DataFrame(
            columns=["cusip", "issuer_name", "shares", "value_usd"]
        ).set_index("cusip")
    placeholders = ",".join("?" * len(accessions))
    frame = pd.read_sql_query(
        f"""
        SELECT cusip, MAX(issuer_name) AS issuer_name,
               SUM(shares) AS shares, SUM(value_usd) AS value_usd
        FROM holdings
        WHERE accession_no IN ({placeholders}) AND share_type = 'SH'
        GROUP BY cusip
        """,
        conn,
        params=accessions,
    )
    return frame.set_index("cusip")


def diff_quarters(conn, cik: int, period_prev: str, period_curr: str) -> pd.DataFrame:
    """All position changes between two quarters, one row per CUSIP.

    change_type: OPENED, EXITED, INCREASED, DECREASED, UNCHANGED.
    Ticker display info is joined after the comparison and plays no
    part in change detection."""
    prev = quarter_positions(conn, cik, period_prev)
    curr = quarter_positions(conn, cik, period_curr)

    merged = prev.join(
        curr, how="outer", lsuffix="_prev", rsuffix="_curr"
    )
    merged["shares_prev"] = merged["shares_prev"].fillna(0).astype(int)
    merged["shares_curr"] = merged["shares_curr"].fillna(0).astype(int)
    merged["share_change"] = merged["shares_curr"] - merged["shares_prev"]

    def classify(row):
        if row["shares_prev"] == 0 and row["shares_curr"] > 0:
            return "OPENED"
        if row["shares_prev"] > 0 and row["shares_curr"] == 0:
            return "EXITED"
        if row["share_change"] > 0:
            return "INCREASED"
        if row["share_change"] < 0:
            return "DECREASED"
        return "UNCHANGED"

    merged["change_type"] = merged.apply(classify, axis=1)
    merged["issuer_name"] = merged["issuer_name_curr"].fillna(
        merged["issuer_name_prev"]
    )
    merged["pct_change_shares"] = (
        100.0 * merged["share_change"] / merged["shares_prev"]
    ).where(merged["shares_prev"] > 0)

    # Display-only mapping join, deliberately after all change logic.
    tickers = pd.read_sql_query(
        "SELECT cusip, ticker, status AS mapping_status FROM cusip_map",
        conn,
    ).set_index("cusip")
    merged = merged.join(tickers, how="left")
    merged["mapping_status"] = merged["mapping_status"].fillna("unmapped")

    columns = [
        "issuer_name", "ticker", "mapping_status", "change_type",
        "shares_prev", "shares_curr", "share_change", "pct_change_shares",
        "value_usd_prev", "value_usd_curr",
    ]
    return (
        merged[columns]
        .sort_values(["change_type", "value_usd_curr"], ascending=[True, False])
    )
