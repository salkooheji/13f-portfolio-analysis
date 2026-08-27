"""Maps tickers to sectors using SEC-published data end to end.

Chain: ticker -> CIK via the SEC's company_tickers.json, then
CIK -> SIC code via the submissions API, then SIC -> division using
the published SIC range structure. Fully documented, no scraping,
and the middle step reuses the throttled EdgarClient.

Trade-off, documented: SIC divisions are broad (GICS-style sectors
are proprietary). Coarse but citable beats fine but scraped.

Tickers with no SEC registrant match (e.g. foreign-listing tickers
recovered by the resolver's fallback strategies) are recorded as
'Unclassified' with source 'no_sec_match', visible rather than
dropped, consistent with the rest of the pipeline."""

import datetime
import logging

logger = logging.getLogger(__name__)

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"

# SIC division ranges, per the published SIC structure.
SIC_DIVISIONS = (
    (100, 999, "Agriculture, Forestry & Fishing"),
    (1000, 1499, "Mining"),
    (1500, 1799, "Construction"),
    (2000, 3999, "Manufacturing"),
    (4000, 4999, "Transportation & Utilities"),
    (5000, 5199, "Wholesale Trade"),
    (5200, 5999, "Retail Trade"),
    (6000, 6799, "Finance, Insurance & Real Estate"),
    (7000, 8999, "Services"),
    (9100, 9999, "Public Administration"),
)


def sic_to_sector(sic: int | None) -> str | None:
    if sic is None:
        return None
    for low, high, name in SIC_DIVISIONS:
        if low <= sic <= high:
            return name
    return None


def _ticker_to_cik(client) -> dict[str, int]:
    """The SEC's official ticker->CIK mapping, one request."""
    data = client.get(COMPANY_TICKERS_URL).json()
    return {
        entry["ticker"].upper(): entry["cik_str"] for entry in data.values()
    }


def map_sectors(conn, client) -> dict:
    """Map every resolved ticker not yet in sector_map.

    Idempotent through the sector_map primary key: a ticker is looked
    up at most once across all runs. Returns summary counts."""
    known = _ticker_to_cik(client)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    pending = [
        row["ticker"]
        for row in conn.execute(
            """
            SELECT DISTINCT m.ticker FROM cusip_map m
            LEFT JOIN sector_map s ON s.ticker = m.ticker
            WHERE m.status = 'resolved' AND m.ticker IS NOT NULL
              AND s.ticker IS NULL
            """
        )
    ]
    logger.info("Mapping sectors for %d tickers", len(pending))

    mapped, unmatched = 0, 0
    for i, ticker in enumerate(pending, start=1):
        if i % 500 == 0:
            logger.info("Sector mapping progress: %d/%d", i, len(pending))
        cik = known.get(ticker.upper())
        sector = None
        if cik is not None:
            submissions = client.get(
                SUBMISSIONS_URL.format(cik10=str(cik).zfill(10))
            ).json()
            sic_raw = submissions.get("sic")
            sic = int(sic_raw) if sic_raw else None
            sector = sic_to_sector(sic)

        if sector is None:
            conn.execute(
                "INSERT INTO sector_map (ticker, sector, source, mapped_at) "
                "VALUES (?, 'Unclassified', 'no_sec_match', ?)",
                (ticker, now),
            )
            unmatched += 1
        else:
            conn.execute(
                "INSERT INTO sector_map (ticker, sector, source, mapped_at) "
                "VALUES (?, ?, 'sec_sic', ?)",
                (ticker, sector, now),
            )
            mapped += 1
        conn.commit()

    return {"mapped": mapped, "unclassified": unmatched}


def sector_coverage(conn) -> dict:
    """Share of resolved-holding dollars carrying a real sector."""
    row = conn.execute(
        """
        SELECT
            SUM(h.value_usd) AS total_usd,
            SUM(CASE WHEN s.source = 'sec_sic' THEN h.value_usd ELSE 0 END)
                AS classified_usd
        FROM holdings h
        JOIN cusip_map m ON m.cusip = h.cusip AND m.status = 'resolved'
        JOIN sector_map s ON s.ticker = m.ticker
        WHERE h.value_usd IS NOT NULL
        """
    ).fetchone()
    total = row["total_usd"] or 0
    classified = row["classified_usd"] or 0
    return {
        "classified_pct_by_value": (
            round(100.0 * classified / total, 2) if total else 0.0
        ),
    }
