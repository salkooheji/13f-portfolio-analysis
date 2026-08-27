"""Streamlit dashboard over the 13F database.

Read-only by design: this app never touches the network and opens
SQLite in read-only mode. Every figure displayed is traceable to
the filings that produced it: the sidebar states the accession
numbers constituting the selected view, and holdings tables carry
per-row source accession numbers."""

import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

from edgar13f.analysis.concentration import (
    concentration,
    option_exposure,
    turnover,
    weighted_positions,
)
from edgar13f.analysis.diffs import diff_quarters, quarter_filings
from edgar13f.analysis.overlap import overlap_pct
from edgar13f.config import load_config

st.set_page_config(page_title="13F Portfolio Analysis", layout="wide")


@st.cache_resource
def connect():
    config = load_config()
    uri = f"file:{config['paths']['database']}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return config, conn


config, conn = connect()
managers = {m["name"]: m["cik"] for m in config["managers"]}

st.sidebar.title("13F Portfolio Analysis")
manager_name = st.sidebar.selectbox("Manager", list(managers))
cik = managers[manager_name]

periods = [
    r["period_of_report"]
    for r in conn.execute(
        "SELECT DISTINCT period_of_report FROM filings "
        "WHERE cik = ? AND parse_status = 'parsed' "
        "ORDER BY period_of_report DESC",
        (cik,),
    )
]
period = st.sidebar.selectbox("Quarter end", periods)
prev_index = periods.index(period) + 1
period_prev = periods[prev_index] if prev_index < len(periods) else None

accessions = quarter_filings(conn, cik, period)
st.sidebar.markdown("**View built from filings:**")
for accession in accessions:
    filed = conn.execute(
        "SELECT filed_date, form_type FROM filings WHERE accession_no = ?",
        (accession,),
    ).fetchone()
    st.sidebar.code(f"{accession}\n{filed['form_type']}, filed {filed['filed_date']}")

st.title(f"{manager_name}")
st.caption(f"Quarter ending {period}")

# --- headline metrics ---
metrics = concentration(conn, cik, period)
options = option_exposure(conn, cik, period)
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Positions", metrics["n_positions"])
col2.metric("Top-10 weight", f"{metrics['top10_weight_pct']}%")
col3.metric("HHI", metrics["hhi"])
col4.metric("Value coverage", f"{metrics['included_value_coverage_pct']}%")
col5.metric("Options (excluded)", f"{options['options_pct_of_raw_value']}%")
if period_prev:
    st.caption(
        f"Turnover vs {period_prev}: "
        f"{turnover(conn, cik, period_prev, period)['turnover_pct']}% "
        "(approximate: quarter-end snapshots conflate trades with price moves)"
    )

tab_holdings, tab_changes, tab_sectors, tab_compare = st.tabs(
    ["Holdings", "Quarterly changes", "Sectors", "Compare managers"]
)

with tab_holdings:
    positions, _ = weighted_positions(conn, cik, period)
    placeholders = ",".join("?" * len(accessions))
    sources = pd.read_sql_query(
        f"""
        SELECT cusip, MIN(accession_no) AS source_accession
        FROM holdings WHERE accession_no IN ({placeholders})
        GROUP BY cusip
        """,
        conn,
        params=accessions,
    ).set_index("cusip")
    tickers = pd.read_sql_query(
        "SELECT cusip, ticker FROM cusip_map WHERE status='resolved'", conn
    ).set_index("cusip")
    table = (
        positions.join(tickers, how="left").join(sources, how="left")
        .assign(weight_pct=lambda d: (100 * d["weight"]).round(2))
        [["issuer_name", "ticker", "shares", "value_usd", "weight_pct",
          "source_accession"]]
    )
    st.dataframe(table, use_container_width=True)
    st.caption(
        "Universe: share-type holdings with verified units and resolved "
        "CUSIPs. Unresolved and option positions are excluded and "
        "measured in the headline metrics."
    )

with tab_changes:
    if period_prev is None:
        st.info("Earliest quarter in the database, no prior quarter to diff.")
    else:
        diff = diff_quarters(conn, cik, period_prev, period)
        changed = diff[diff["change_type"] != "UNCHANGED"]
        st.subheader(f"Changes vs {period_prev}")
        counts = changed["change_type"].value_counts()
        st.write(
            "  |  ".join(f"{k}: {v}" for k, v in counts.items()) or "No changes"
        )
        st.dataframe(changed, use_container_width=True)
        st.caption(
            "Changes computed on share counts per CUSIP as reported in "
            "filings. Mapping status shown per row; mapping failures "
            "cannot appear as trades. Share counts are not adjusted for "
            "corporate actions such as splits."
        )

with tab_sectors:
    placeholders = ",".join("?" * len(accessions))
    sector_split = pd.read_sql_query(
        f"""
        SELECT s.sector, SUM(h.value_usd) AS value_usd
        FROM holdings h
        JOIN cusip_map m ON m.cusip = h.cusip AND m.status = 'resolved'
        JOIN sector_map s ON s.ticker = m.ticker
        WHERE h.accession_no IN ({placeholders}) AND h.put_call IS NULL
        GROUP BY s.sector ORDER BY value_usd DESC
        """,
        conn,
        params=accessions,
    )
    fig = px.pie(sector_split, names="sector", values="value_usd", hole=0.45)
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Sector source: SEC SIC codes mapped to SIC divisions "
        "(documented in README). SIC is coarse; GICS-style sectors are "
        "proprietary and deliberately not used."
    )

with tab_compare:
    other_name = st.selectbox(
        "Compare against", [m for m in managers if m != manager_name]
    )
    other_cik = managers[other_name]
    forward = overlap_pct(conn, cik, other_cik, period)
    backward = overlap_pct(conn, other_cik, cik, period)
    col_a, col_b = st.columns(2)
    col_a.metric(
        f"{manager_name} overlap with {other_name}",
        f"{forward['overlap_pct_of_a_by_weight']}%",
        help="Share of this manager's book, by weight, in names the "
             "other manager also holds. Directional by definition.",
    )
    col_b.metric(
        f"{other_name} overlap with {manager_name}",
        f"{backward['overlap_pct_of_a_by_weight']}%",
    )
    st.caption(
        f"{forward['n_shared_positions']} shared positions in quarter "
        f"{period}."
    )
