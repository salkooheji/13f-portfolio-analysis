"""Parses the SGML envelope and the primary_doc.xml cover page.

The cover page is where a filing describes itself: which quarter it
reports, whether it amends an earlier filing and how, and whether
holdings were omitted under confidential treatment. We treat the
cover page as authoritative over the submissions index metadata.
"""

import re
from lxml import etree

# Matches each <DOCUMENT>...</DOCUMENT> block in the SGML envelope.
DOCUMENT_RE = re.compile(rb"<DOCUMENT>(.*?)</DOCUMENT>", re.DOTALL)
TYPE_RE = re.compile(rb"<TYPE>([^\r\n<]+)")
TEXT_RE = re.compile(rb"<TEXT>(.*?)</TEXT>", re.DOTALL)
# EDGAR wraps XML documents in one more layer inside <TEXT>:
# <XML>\n<?xml ...?>...</XML>. lxml requires the declaration at
# byte zero, so this wrapper must be peeled before parsing.
XML_WRAPPER_RE = re.compile(rb"<XML>\s*(.*?)\s*</XML>", re.DOTALL | re.IGNORECASE)


def split_documents(raw: bytes) -> list[dict]:
    """Split a complete submission file into its component documents.

    Returns a list of {"type": str, "content": bytes}. Works on bytes
    throughout because filings mix encodings, and decoding too early
    is how parsers die on stray characters.
    """
    documents = []
    for block in DOCUMENT_RE.finditer(raw):
        body = block.group(1)
        type_match = TYPE_RE.search(body)
        text_match = TEXT_RE.search(body)
        if type_match and text_match:
            documents.append(
                {
                    "type": type_match.group(1).decode("ascii", "replace").strip(),
                    "content": text_match.group(1).strip(),
                }
            )
    return documents


def _find_text(root, tag: str) -> str | None:
    """Find the first element with this local tag name, namespace-blind.

    Cover pages declare XML namespaces, so a plain find('coverPage')
    matches nothing. local-name() sidesteps the namespace entirely.
    """
    hits = root.xpath(f"//*[local-name()='{tag}']")
    if hits and hits[0].text is not None:
        return hits[0].text.strip()
    return None


def _to_iso(mm_dd_yyyy: str) -> str:
    """Cover pages write dates as MM-DD-YYYY; the database uses ISO."""
    month, day, year = mm_dd_yyyy.split("-")
    return f"{year}-{month}-{day}"


def parse_cover(raw: bytes) -> dict:
    """Extract period, amendment info, and the CT flag from a filing.

    Raises ValueError if no primary_doc cover page is present, because
    a 13F without a cover page is not something to guess about.
    """
    cover_xml = None
    for doc in split_documents(raw):
        if doc["type"].startswith("13F-HR"):
            cover_xml = doc["content"]
            break
    if cover_xml is None:
        raise ValueError("No 13F-HR primary document found in submission")

    wrapper = XML_WRAPPER_RE.search(cover_xml)
    if wrapper:
        cover_xml = wrapper.group(1)

    root = etree.fromstring(cover_xml)

    period_raw = _find_text(root, "reportCalendarOrQuarter")
    if period_raw is None:
        raise ValueError("Cover page has no reportCalendarOrQuarter")

    is_amendment = (_find_text(root, "isAmendment") or "false").lower() == "true"
    amendment_type = _find_text(root, "amendmentType") if is_amendment else None

    ct_omitted = (
        (_find_text(root, "isConfidentialOmitted") or "false").lower() == "true"
    )

    return {
        "period_of_report": _to_iso(period_raw),
        "is_amendment": is_amendment,
        "amendment_type": amendment_type,  # RESTATEMENT or NEW HOLDINGS
        "is_ct_request": ct_omitted,
    }
