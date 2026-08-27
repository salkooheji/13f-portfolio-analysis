"""The only module that talks to sec.gov.

Centralizes the two EDGAR fair-access requirements:
  1. An identifying User-Agent on every request.
  2. A rate limit safely under the SEC's 10 requests/second cap.

Every other module makes SEC requests exclusively through EdgarClient,
so compliance is guaranteed structurally rather than by convention.
"""

import time

import requests

# 0.15s between requests = ~6.7 req/s, deliberate headroom under
# the SEC's 10 req/s limit. Clock jitter is cheaper than an IP block.
MIN_REQUEST_INTERVAL_SECONDS = 0.15

MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 2.0  # waits 2s, then 4s between attempts

# Status codes worth retrying: rate-limited or transient server errors.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class EdgarClient:
    """HTTP client for SEC EDGAR with throttling and retries."""

    def __init__(self, user_agent: str):
        if not user_agent or "@" not in user_agent:
            raise ValueError(
                "sec_user_agent must be set in config.yaml as 'Name email'. "
                "The SEC rejects unidentified automated traffic."
            )
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})
        self._last_request_time = 0.0

    def _throttle(self) -> None:
        """Sleep just enough to keep the minimum interval between requests."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL_SECONDS:
            time.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)
        self._last_request_time = time.monotonic()

    def get(self, url: str) -> requests.Response:
        """GET a URL politely: throttled, retried on transient failures.

        Returns the successful Response. Raises RuntimeError after
        exhausting retries, or immediately for non-retryable errors
        like 404, which retrying would never fix.
        """
        last_error = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._throttle()
            try:
                response = self._session.get(url, timeout=30)
            except requests.RequestException as exc:
                # Network-level failure (DNS, timeout, dropped connection).
                last_error = exc
            else:
                if response.status_code == 200:
                    return response
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    raise RuntimeError(
                        f"EDGAR returned {response.status_code} for {url}"
                    )
                last_error = RuntimeError(
                    f"EDGAR returned {response.status_code} for {url}"
                )
            if attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        raise RuntimeError(
            f"Giving up on {url} after {MAX_ATTEMPTS} attempts: {last_error}"
        )
