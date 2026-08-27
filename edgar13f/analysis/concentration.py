"""Concentration and turnover metrics for one manager-quarter.

Weights are computed over the included universe: SH-type holdings
with verified USD values and resolved CUSIPs. The share of raw filing
value that universe captures is reported alongside every metric, so
'weights sum to 100%' is true by construction and the exclusions are
measured, not hidden."""

import pandas as pd

from edgar13f.analysis.diffs import quarter_positions


def weighted_positions(conn, cik: int, period: str) -> tuple[pd.DataFrame, float]:
    """Positions with weights, plus included-share-of-raw-total."""
    positions = quarter_positions(conn, cik, period)
    raw_total = positions["value_usd"].sum()

    resolved = pd.read_sql_query(
        "SELECT cusip FROM cusip_map WHERE status = 'resolved'", conn
    )["cusip"]
    included = positions[
        positions.index.isin(resolved) & positions["value_usd"].notna()
    ].copy()
    included_total = included["value_usd"].sum()
    included["weight"] = included["value_usd"] / included_total

    coverage = included_total / raw_total if raw_total else 0.0
    return included.sort_values("weight", ascending=False), coverage


def concentration(conn, cik: int, period: str) -> dict:
    positions, coverage = weighted_positions(conn, cik, period)
    weights = positions["weight"]
    return {
        "period": period,
        "n_positions": len(positions),
        "top10_weight_pct": round(100.0 * float(weights.head(10).sum()), 2),
        "hhi": round(float((weights ** 2).sum()), 4),
        "included_value_coverage_pct": round(100.0 * float(coverage), 2),
    }


def turnover(conn, cik: int, period_prev: str, period_curr: str) -> dict:
    """Approximate turnover: sum of absolute value changes / 2, over
    average book value. Documented caveat: value changes conflate
    trades with price moves; this is the best 13F data alone allows."""
    prev, _ = weighted_positions(conn, cik, period_prev)
    curr, _ = weighted_positions(conn, cik, period_curr)
    merged = prev[["value_usd"]].join(
        curr[["value_usd"]], how="outer", lsuffix="_prev", rsuffix="_curr"
    ).fillna(0.0)
    traded = (merged["value_usd_curr"] - merged["value_usd_prev"]).abs().sum() / 2.0
    average_book = (
        merged["value_usd_prev"].sum() + merged["value_usd_curr"].sum()
    ) / 2.0
    return {
        "turnover_pct": (
            round(100.0 * float(traded / average_book), 2) if average_book else 0.0
        ),
    }

def option_exposure(conn, cik: int, period: str) -> dict:
    """Share of the quarter's raw filing value sitting in option
    positions, which are excluded from share-based analysis. 13F
    reports option positions at underlying notional value, so this
    overstates capital at risk; it is reported for visibility, not
    treated as portfolio weight."""
    from edgar13f.analysis.diffs import quarter_filings

    accessions = quarter_filings(conn, cik, period)
    if not accessions:
        return {"options_pct_of_raw_value": 0.0, "n_option_rows": 0}
    placeholders = ",".join("?" * len(accessions))
    row = conn.execute(
        f"""
        SELECT
            SUM(value_usd) AS total,
            SUM(CASE WHEN put_call IS NOT NULL THEN value_usd ELSE 0 END)
                AS options,
            SUM(CASE WHEN put_call IS NOT NULL THEN 1 ELSE 0 END)
                AS n_rows
        FROM holdings
        WHERE accession_no IN ({placeholders})
        """,
        accessions,
    ).fetchone()
    total = row["total"] or 0
    return {
        "options_pct_of_raw_value": (
            round(100.0 * (row["options"] or 0) / total, 2) if total else 0.0
        ),
        "n_option_rows": row["n_rows"] or 0,
    }