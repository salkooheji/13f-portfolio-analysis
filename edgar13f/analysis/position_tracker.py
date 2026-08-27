"""Tracks one security across every filer and quarter.

Answers: who was accumulating and who was distributing this name
over time. Pure query over the holdings CUSIP index; shares are as
reported, with the usual corporate-actions caveat."""

import pandas as pd

from edgar13f.analysis.diffs import quarter_filings


def track_position(conn, cusip: str, ciks: list[int],
                   periods: list[str]) -> pd.DataFrame:
    """Share counts per manager per quarter for one CUSIP.
    Rows: periods. Columns: CIKs. Zero means not held."""
    data = {}
    for cik in ciks:
        series = {}
        for period in periods:
            accessions = quarter_filings(conn, cik, period)
            if not accessions:
                series[period] = 0
                continue
            placeholders = ",".join("?" * len(accessions))
            row = conn.execute(
                f"""
                SELECT COALESCE(SUM(shares), 0) AS shares
                FROM holdings
                WHERE accession_no IN ({placeholders})
                  AND cusip = ? AND share_type = 'SH'
                  AND put_call IS NULL
                """,
                accessions + [cusip],
            ).fetchone()
            series[period] = row["shares"]
        data[cik] = series
    return pd.DataFrame(data).sort_index()
