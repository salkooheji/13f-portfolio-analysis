"""Resolves CUSIPs to tickers and company names via OpenFIGI.

Design contract, per the README: unresolved CUSIPs are recorded, not
dropped. Every distinct CUSIP in holdings ends up with a row in
cusip_map, status 'resolved' or 'unresolved', and the resolution rate
is a query over that table. Downstream analysis joins through
cusip_map and decides explicitly what to do with unresolved names.

The cusip_map primary key doubles as a cache: a CUSIP is queried at
most once across all runs, so scheduled reruns only resolve
securities never seen before.
"""

import datetime
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"

# Without an API key OpenFIGI allows small, slow batches. With a free
# key (env var OPENFIGI_API_KEY) both limits rise substantially.
BATCH_SIZE_NO_KEY = 10
BATCH_SIZE_WITH_KEY = 100
SECONDS_BETWEEN_BATCHES_NO_KEY = 2.5
SECONDS_BETWEEN_BATCHES_WITH_KEY = 0.3


def _unmapped_cusips(conn) -> list[str]:
    """Distinct CUSIPs present in holdings but absent from cusip_map."""
    rows = conn.execute(
        """
        SELECT DISTINCT h.cusip FROM holdings h
        LEFT JOIN cusip_map m ON m.cusip = h.cusip
        WHERE m.cusip IS NULL
        ORDER BY h.cusip
        """
    ).fetchall()
    return [row["cusip"] for row in rows]


def _query_openfigi(
    cusips: list[str],
    api_key: str | None,
    id_type: str = "ID_CUSIP",
    exch_code: str | None = "US",
) -> list[dict]:
    """One batched OpenFIGI request. Returns one result dict per input.

    OpenFIGI's response is positional: result[i] answers cusips[i],
    as either {'data': [...]} or {'error'/'warning': ...}.
    exch_code=None omits the exchange constraint, which is needed to
    match delisted securities.
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key
    jobs = []
    for cusip in cusips:
        job = {"idType": id_type, "idValue": cusip}
        if exch_code:
            job["exchCode"] = exch_code
        jobs.append(job)
    response = requests.post(OPENFIGI_URL, json=jobs, headers=headers, timeout=30)
    if response.status_code == 429:
        # Rate limited: wait and retry once. OpenFIGI's windows are
        # short, so a single generous sleep usually clears it.
        logger.warning("OpenFIGI rate limit hit, sleeping 15s")
        time.sleep(15)
        response = requests.post(OPENFIGI_URL, json=jobs, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def resolve_cusips(conn) -> dict:
    """Resolve all unmapped CUSIPs. Returns summary counts.

    Checkpoints after every batch: results are committed as they
    arrive, so an interruption loses at most one batch and a rerun
    continues where this one stopped.
    """
    api_key = os.environ.get("OPENFIGI_API_KEY")
    batch_size = BATCH_SIZE_WITH_KEY if api_key else BATCH_SIZE_NO_KEY
    pause = (
        SECONDS_BETWEEN_BATCHES_WITH_KEY
        if api_key
        else SECONDS_BETWEEN_BATCHES_NO_KEY
    )

    pending = _unmapped_cusips(conn)
    logger.info(
        "Resolving %d unmapped CUSIPs (batch size %d, key %s)",
        len(pending),
        batch_size,
        "present" if api_key else "absent",
    )

    resolved_count, unresolved_count = 0, 0
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        results = _query_openfigi(batch, api_key)

        rows = []
        for cusip, result in zip(batch, results):
            data = result.get("data") or []
            if data:
                best = data[0]
                rows.append(
                    (cusip, best.get("ticker"), best.get("name"),
                     "openfigi", "resolved", now)
                )
                resolved_count += 1
            else:
                rows.append((cusip, None, None, "openfigi", "unresolved", now))
                unresolved_count += 1

        conn.executemany(
            """
            INSERT INTO cusip_map
                (cusip, ticker, company_name, source, status, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        if start + batch_size < len(pending):
            time.sleep(pause)

    return {
        "newly_resolved": resolved_count,
        "newly_unresolved": unresolved_count,
    }

def _retry_batch(conn, cusips: list[str], api_key: str | None,
                 id_type: str, exch_code: str | None, source_label: str,
                 batch_size: int, pause: float) -> int:
    """Retry a set of unresolved CUSIPs under one fallback strategy.

    Updates rows that now resolve, labeling them with the strategy
    that succeeded. Returns how many resolved."""
    recovered = 0
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for start in range(0, len(cusips), batch_size):
        batch = cusips[start : start + batch_size]
        results = _query_openfigi(batch, api_key, id_type, exch_code)
        for cusip, result in zip(batch, results):
            data = result.get("data") or []
            if data:
                best = data[0]
                conn.execute(
                    """
                    UPDATE cusip_map
                    SET ticker = ?, company_name = ?, source = ?,
                        status = 'resolved', resolved_at = ?
                    WHERE cusip = ?
                    """,
                    (best.get("ticker"), best.get("name"),
                     source_label, now, cusip),
                )
                recovered += 1
        conn.commit()
        if start + batch_size < len(cusips):
            time.sleep(pause)
    return recovered


def refine_unresolved(conn) -> dict:
    """Second-pass resolution for CUSIPs the strict query missed.

    Strategy funnel, loosening one constraint at a time:
      1. Letter-prefixed identifiers are CINS, the international
         extension of CUSIP, and need idType ID_CINS.
      2. Anything still unresolved is retried without the US exchange
         constraint, which is what matches delisted securities.
    Each recovered row's source records the strategy that found it."""
    api_key = os.environ.get("OPENFIGI_API_KEY")
    batch_size = BATCH_SIZE_WITH_KEY if api_key else BATCH_SIZE_NO_KEY
    pause = (
        SECONDS_BETWEEN_BATCHES_WITH_KEY
        if api_key
        else SECONDS_BETWEEN_BATCHES_NO_KEY
    )

    def unresolved() -> list[str]:
        return [
            row["cusip"]
            for row in conn.execute(
                "SELECT cusip FROM cusip_map WHERE status = 'unresolved'"
            )
        ]

    summary = {}

    cins = [c for c in unresolved() if c[0].isalpha()]
    summary["recovered_as_cins"] = _retry_batch(
        conn, cins, api_key, "ID_CINS", "US", "openfigi_cins",
        batch_size, pause,
    )

    remaining = unresolved()
    summary["recovered_without_exchange"] = _retry_batch(
        conn, remaining, api_key, "ID_CUSIP", None, "openfigi_no_exch",
        batch_size, pause,
    )

    still_cins = [c for c in unresolved() if c[0].isalpha()]
    summary["recovered_cins_without_exchange"] = _retry_batch(
        conn, still_cins, api_key, "ID_CINS", None, "openfigi_cins_no_exch",
        batch_size, pause,
    )

    summary["still_unresolved"] = len(unresolved())
    return summary


def resolution_rate(conn) -> dict:
    """The audit number, weighted two ways.

    By distinct CUSIP: what fraction of securities resolved.
    By value: what fraction of portfolio dollars sits in resolved
    names, which is what actually matters for portfolio analysis and
    is typically much higher, since failures concentrate in tiny
    positions and odd instruments.
    """
    by_cusip = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END) AS resolved
        FROM cusip_map
        """
    ).fetchone()

    by_value = conn.execute(
        """
        SELECT
            SUM(h.value_usd) AS total_usd,
            SUM(CASE WHEN m.status = 'resolved' THEN h.value_usd ELSE 0 END)
                AS resolved_usd
        FROM holdings h
        JOIN cusip_map m ON m.cusip = h.cusip
        WHERE h.value_usd IS NOT NULL
        """
    ).fetchone()

    total, resolved = by_cusip["total"], by_cusip["resolved"] or 0
    total_usd = by_value["total_usd"] or 0
    resolved_usd = by_value["resolved_usd"] or 0
    return {
        "cusips_total": total,
        "cusips_resolved": resolved,
        "rate_by_cusip_pct": round(100.0 * resolved / total, 2) if total else 0.0,
        "rate_by_value_pct": (
            round(100.0 * resolved_usd / total_usd, 2) if total_usd else 0.0
        ),
    }
