"""Parses the information table of a 13F filing into holdings rows.

Every field is taken as reported. No unit conversion happens here:
raw_value stores the number exactly as printed, and unit verification
is a separate, later concern. Parsing and interpreting are different
jobs, and mixing them is how silent errors are born.
"""

from lxml import etree

from edgar13f.parse.coverpage import split_documents, XML_WRAPPER_RE


def _first_text(element, tag: str) -> str | None:
    """First descendant with this local tag name, namespace-blind."""
    hits = element.xpath(f".//*[local-name()='{tag}']")
    if hits and hits[0].text is not None:
        return hits[0].text.strip()
    return None


def _to_int(text: str | None) -> int | None:
    """Parse an integer that may carry commas or decimals.

    Filings occasionally write 1,234 or 1234.0. int(float(x)) handles
    both. Returns None for missing values rather than guessing zero,
    because zero is a claim and None is an admission.
    """
    if text is None or text == "":
        return None
    return int(float(text.replace(",", "")))


def parse_infotable(raw: bytes) -> list[dict]:
    """Extract all holdings from a filing's information table.

    Raises ValueError if no information table document exists, which
    is a real condition: a 13F-HR cover page can be filed with zero
    reported holdings, and the caller decides what that means.
    """
    table_xml = None
    for doc in split_documents(raw):
        if doc["type"].upper().startswith("INFORMATION TABLE"):
            table_xml = doc["content"]
            break
    if table_xml is None:
        raise ValueError("No INFORMATION TABLE document found in submission")

    wrapper = XML_WRAPPER_RE.search(table_xml)
    if wrapper:
        table_xml = wrapper.group(1)

    root = etree.fromstring(table_xml)
    rows = []
    for index, entry in enumerate(root.xpath("//*[local-name()='infoTable']")):
        issuer = _first_text(entry, "nameOfIssuer")
        cusip = _first_text(entry, "cusip")
        value = _to_int(_first_text(entry, "value"))
        if issuer is None or cusip is None or value is None:
            raise ValueError(
                f"infoTable row {index} missing a required field "
                f"(issuer={issuer!r}, cusip={cusip!r}, value={value!r})"
            )
        rows.append(
            {
                "row_index": index,
                "issuer_name": issuer,
                "class_title": _first_text(entry, "titleOfClass"),
                "cusip": cusip.upper(),
                "raw_value": value,
                "shares": _to_int(_first_text(entry, "sshPrnamt")),
                "share_type": _first_text(entry, "sshPrnamtType"),
                # Present only for option positions. A filing row with
                # putCall set reports the option's underlying shares,
                # not an equity holding, and must not be treated as one.
                "put_call": _first_text(entry, "putCall"),
            }
        )
    return rows
