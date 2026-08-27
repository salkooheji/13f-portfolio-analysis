"""Database layer: connection handling and schema creation.

Every other module gets its database access through get_connection().
The schema is created with CREATE TABLE IF NOT EXISTS, so calling
init_db() repeatedly is safe. That property matters: the pipeline
calls it on every run, which means a clean clone bootstraps itself
with no manual setup step.
"""

import sqlite3
from pathlib import Path


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open a connection to the SQLite database at db_path.

    Creates the parent directory if it does not exist yet, so a
    clean clone can run without any manual folder creation.
    Foreign keys are enforced per connection in SQLite, so we
    switch them on here rather than trusting callers to remember.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    # Rows behave like dicts (row["cusip"]) instead of bare tuples,
    # which keeps downstream code readable.
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they do not already exist."""
    conn.executescript(
        """
        -- One row per filing, keyed by the SEC accession number.
        -- A filing is immutable once ingested: amendments become new
        -- rows, and supersedes links them to what they amend.
        CREATE TABLE IF NOT EXISTS filings (
            accession_no      TEXT PRIMARY KEY,
            cik               INTEGER NOT NULL,
            form_type         TEXT NOT NULL,      -- 13F-HR or 13F-HR/A
            period_of_report  TEXT NOT NULL,      -- quarter end, YYYY-MM-DD
            filed_date        TEXT NOT NULL,      -- YYYY-MM-DD
            amendment_type    TEXT,               -- RESTATEMENT, NEW HOLDINGS,
                                                  -- or NULL for originals
            supersedes        TEXT,               -- accession_no this amends,
                                                  -- NULL for originals
            is_ct_request     INTEGER NOT NULL DEFAULT 0,
                                                  -- confidential treatment flag
            value_unit        TEXT,               -- THOUSANDS or DOLLARS,
                                                  -- NULL until verified
            raw_path          TEXT NOT NULL,      -- where the raw filing lives
            parse_status      TEXT NOT NULL DEFAULT 'pending',
                                                  -- pending, parsed, failed
            parse_error       TEXT,               -- reason if failed
            ingested_at       TEXT NOT NULL       -- ISO timestamp
        );

        -- One row per position per filing, exactly as reported.
        CREATE TABLE IF NOT EXISTS holdings (
            accession_no      TEXT NOT NULL REFERENCES filings(accession_no),
            row_index         INTEGER NOT NULL,   -- position within the filing
            issuer_name       TEXT NOT NULL,
            class_title       TEXT,               -- e.g. COM, CL A
            cusip             TEXT NOT NULL,
            raw_value         INTEGER NOT NULL,   -- as printed in the filing
            value_usd         INTEGER,            -- after unit verification,
                                                  -- NULL until units known
            shares            INTEGER,
            share_type        TEXT,               -- SH or PRN
            put_call          TEXT,               -- Put, Call, or NULL for
                                                  -- an actual equity holding
            PRIMARY KEY (accession_no, row_index)
        );

        -- CUSIP resolution results, including failures. A CUSIP that
        -- could not be resolved gets a row with status 'unresolved',
        -- which is what keeps failures visible instead of silent.
        CREATE TABLE IF NOT EXISTS cusip_map (
            cusip             TEXT PRIMARY KEY,
            ticker            TEXT,
            company_name      TEXT,
            source            TEXT NOT NULL,      -- e.g. openfigi, sec_list
            status            TEXT NOT NULL,      -- resolved or unresolved
            resolved_at       TEXT NOT NULL
        );

        -- Sector per ticker, with the mapping source recorded because
        -- the audit asks for it.
        CREATE TABLE IF NOT EXISTS sector_map (
            ticker            TEXT PRIMARY KEY,
            sector            TEXT NOT NULL,
            source            TEXT NOT NULL,
            mapped_at         TEXT NOT NULL
        );

        -- One row per pipeline run. This table is the evidence that
        -- automation works: the audit inspects exactly these fields.
        CREATE TABLE IF NOT EXISTS run_log (
            run_id            INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at        TEXT NOT NULL,
            finished_at       TEXT,
            filings_fetched   INTEGER NOT NULL DEFAULT 0,
            filings_skipped   INTEGER NOT NULL DEFAULT 0,
            parse_failures    INTEGER NOT NULL DEFAULT 0,
            runtime_seconds   REAL,
            triggered_by      TEXT NOT NULL       -- manual or scheduled
        );

        -- Lookups the pipeline and dashboard will do constantly.
        CREATE INDEX IF NOT EXISTS idx_filings_cik_period
            ON filings(cik, period_of_report);
        CREATE INDEX IF NOT EXISTS idx_holdings_cusip
            ON holdings(cusip);
        """
    )
    conn.commit()
