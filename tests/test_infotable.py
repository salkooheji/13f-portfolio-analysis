"""Parser tests against real filing fragments.

The fixtures are verbatim fragments of actual EDGAR filings, so
these tests exercise real namespaces, wrappers, and field quirks."""

from pathlib import Path

import pytest

from edgar13f.parse.infotable import parse_infotable

FIXTURES = Path(__file__).parent / "fixtures"


def wrap_as_submission(infotable: bytes) -> bytes:
    """Minimal SGML envelope so parse_infotable sees a full filing."""
    return (
        b"<SEC-DOCUMENT>\n<DOCUMENT>\n<TYPE>INFORMATION TABLE\n"
        b"<TEXT>\n" + infotable + b"\n</TEXT>\n</DOCUMENT>\n</SEC-DOCUMENT>"
    )


def test_parses_scion_book_with_put_rows():
    raw = wrap_as_submission(
        (FIXTURES / "scion_q2_2023_infotable.txt").read_bytes()
    )
    rows = parse_infotable(raw)
    assert len(rows) > 0
    puts = [r for r in rows if r["put_call"] == "Put"]
    assert len(puts) == 2, "the famous SPY and QQQ puts must be flagged"
    equities = [r for r in rows if r["put_call"] is None]
    assert all(r["cusip"] for r in equities)
    assert all(isinstance(r["raw_value"], int) for r in rows)


def test_parses_berkshire_thousands_era_book():
    raw = wrap_as_submission(
        (FIXTURES / "brk_q3_2022_infotable.txt").read_bytes()
    )
    rows = parse_infotable(raw)
    assert len(rows) > 100
    apple = [r for r in rows if r["cusip"] == "037833100"]
    assert apple, "Berkshire without Apple in 2022 would mean misparsing"


def test_missing_information_table_fails_loudly():
    with pytest.raises(ValueError):
        parse_infotable(b"<SEC-DOCUMENT>no documents here</SEC-DOCUMENT>")
