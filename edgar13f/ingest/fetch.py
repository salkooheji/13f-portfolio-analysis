"""Discovers and downloads 13F filings for configured managers.

Idempotency contract: a filing already present in the filings table
is never downloaded or inserted again. Running ingestion twice must
change nothing, which the audit tests explicitly.
"""

import datetime
import logging
from pathlib import Path

from edgar13f.ingest.edgar_client import EdgarClient

logger = logging.getLogger(__name__)

FORMS_OF_INTEREST = {"13F-HR", "13F-HR/A"}

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
FILING_URL = (
    "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{acc}.txt"
)


def padded_cik(cik: int) -> str:
    """The submissions API wants the CIK zero-padded to 10 digits."""
    return str(cik).zfill(10)


def list_13f_filings(client: EdgarClient, cik: int, start: str, end: str) -> list[dict]:
    """Return metadata for this filer's 13F filings within [start, end].

    Dates are ISO strings and compare correctly as text. reportDate is
    the quarter end the filing describes, which is what our window is
    defined over. filingDate, when it was submitted, can be up to 45
    days later and is stored but not filtered on.
    """
    url = SUBMISSIONS_URL.format(cik10=padded_cik(cik))
    data = client.get(url).json()
    recent = data["filings"]["recent"]

    oldest_report = min((d for d in recent["reportDate"] if d), default="9999-12-31")
    if oldest_report > start:
        logger.warning(
            "CIK %s: submissions 'recent' window starts at %s, after our "
            "start %s. Older filings would need the paginated archive, "
            "which this project deliberately does not fetch.",
            cik, oldest_report, start,
        )

    filings = []
    rows = zip(
        recent["form"],
        recent["accessionNumber"],
        recent["filingDate"],
        recent["reportDate"],
    )
    for form, accession, filed, report in rows:
        if form in FORMS_OF_INTEREST and start <= report <= end:
            filings.append(
                {
                    "form_type": form,
                    "accession_no": accession,
                    "filed_date": filed,
                    "period_of_report": report,
                }
            )
    return filings


def download_filing(client: EdgarClient, cik: int, accession_no: str, raw_dir: str) -> str:
    """Download the complete submission text file, byte for byte.

    Written exactly as received: raw archival first, parsing later,
    so we can always re-parse without re-downloading.
    """
    url = FILING_URL.format(
        cik=cik, acc_nodash=accession_no.replace("-", ""), acc=accession_no
    )
    response = client.get(url)

    target_dir = Path(raw_dir) / str(cik)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{accession_no}.txt"
    target_path.write_bytes(response.content)
    return str(target_path)


def ingest_manager(conn, client: EdgarClient, cik: int, raw_dir: str,
                   start: str, end: str) -> tuple[int, int]:
    """Fetch all missing 13F filings for one manager.

    Returns (fetched, skipped). The skip check against the filings
    table is the idempotency mechanism; the primary key on
    accession_no is the structural backstop beneath it.
    """
    fetched, skipped = 0, 0
    for meta in list_13f_filings(client, cik, start, end):
        exists = conn.execute(
            "SELECT 1 FROM filings WHERE accession_no = ?",
            (meta["accession_no"],),
        ).fetchone()
        if exists:
            skipped += 1
            continue

        raw_path = download_filing(client, cik, meta["accession_no"], raw_dir)
        conn.execute(
            """
            INSERT INTO filings
                (accession_no, cik, form_type, period_of_report,
                 filed_date, raw_path, parse_status, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                meta["accession_no"],
                cik,
                meta["form_type"],
                meta["period_of_report"],
                meta["filed_date"],
                raw_path,
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        fetched += 1
        logger.info("Fetched %s %s (%s)", cik, meta["accession_no"], meta["form_type"])
    return fetched, skipped


def run_ingestion(conn, client: EdgarClient, config: dict) -> tuple[int, int]:
    """Ingest every configured manager. Returns totals (fetched, skipped)."""
    total_fetched, total_skipped = 0, 0
    for manager in config["managers"]:
        fetched, skipped = ingest_manager(
            conn,
            client,
            manager["cik"],
            config["paths"]["raw_dir"],
            config["quarters"]["start"],
            config["quarters"]["end"],
        )
        logger.info(
            "%s: %d fetched, %d skipped", manager["name"], fetched, skipped
        )
        total_fetched += fetched
        total_skipped += skipped
    return total_fetched, total_skipped
