"""One-command pipeline: ingest, parse, normalize, log.

Usage:
    python run_pipeline.py                 (manual run)
    python run_pipeline.py --triggered-by scheduled

Every stage is idempotent, so this script is safe to run at any
time, from a clean clone or a populated database alike. A clean
clone bootstraps itself completely; a repeat run fetches and
processes only what is new."""

import argparse
import logging
import sys

from edgar13f.config import load_config
from edgar13f.db import get_connection, init_db
from edgar13f.ingest.edgar_client import EdgarClient
from edgar13f.ingest.fetch import run_ingestion
from edgar13f.normalize.cusip_resolver import (
    refine_unresolved,
    resolve_cusips,
    resolution_rate,
)
from edgar13f.normalize.sectors import map_sectors, sector_coverage
from edgar13f.normalize.units import apply_unit_verification
from edgar13f.parse.apply_covers import apply_cover_metadata
from edgar13f.parse.apply_infotables import apply_infotables, parse_coverage
from edgar13f.run_logger import RunLogger


def main() -> int:
    parser = argparse.ArgumentParser(description="13F pipeline")
    parser.add_argument("--triggered-by", default="manual",
                        choices=["manual", "scheduled"])
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("pipeline")

    config = load_config()
    conn = get_connection(config["paths"]["database"])
    init_db(conn)
    client = EdgarClient(config["sec_user_agent"])

    with RunLogger(conn, args.triggered_by) as run:
        logger.info("Stage 1/4: ingestion")
        fetched, skipped = run_ingestion(conn, client, config)
        run.filings_fetched, run.filings_skipped = fetched, skipped

        logger.info("Stage 2/4: parsing")
        cover_failures = apply_cover_metadata(conn)
        parsed, parse_failed = apply_infotables(conn)
        run.parse_failures = cover_failures + parse_failed

        logger.info("Stage 3/4: normalization")
        verified, undecided = apply_unit_verification(conn)
        resolve_cusips(conn)
        refine_unresolved(conn)
        map_sectors(conn, client)

        logger.info("Stage 4/4: summary")
        logger.info("Ingest: %d fetched, %d skipped", fetched, skipped)
        logger.info("Parse coverage: %s", parse_coverage(conn))
        logger.info("Units: %d verified, %d undecided", verified, undecided)
        logger.info("CUSIP resolution: %s", resolution_rate(conn))
        logger.info("Sector coverage: %s", sector_coverage(conn))

    logger.info("Run %d complete", run.run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
    