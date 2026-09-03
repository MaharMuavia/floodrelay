"""NDMA daily monsoon situation report.

The National Disaster Management Authority publishes a Daily Situation Report
through the monsoon: province and district figures for casualties, houses
damaged, livestock lost, roads and bridges. It is the closest thing Pakistan has
to an authoritative live damage picture, and it is published as a PDF.

This is the most fragile module in the project
----------------------------------------------
There is no API. There is a listing page and a PDF, both of which NDMA can
restyle at any time without notice. So the design is defensive in one specific
way: **the province table is the anchor, and if it cannot be found the parse
fails loudly with the report number and a link.** It does not fall back to
yesterday's report, and it does not emit a zero that looks measured. A wrong
number on a relief console is worse than a visible gap.

Cost discipline: the listing is 36 KB and is checked every time; the PDF is
3.1 MB and is fetched only when the report number changes -- roughly once a day.
Scraping a government site is a courtesy relationship and that budget is what
keeps it defensible.

Nothing here influences an urgency score.
"""

from __future__ import annotations

import html
import re
from typing import Any

from strands import tool

from ...config import get_settings
from ...store.table import Table, get_table, ndma_pk
from ._http import get_bytes, get_text

CACHE_SK = "CACHE"

PROVINCES = ("GB", "KP", "Punjab", "Sindh", "Balochistan", "AJ&K", "ICT")

# Anchors. If these lines are gone, NDMA changed the layout and we must not
# guess at what replaced them.
PROVINCE_TABLE_ANCHOR = re.compile(r"Province\s+Roads\s*\(KMs\)\s+Bridges", re.I)
DISTRICT_TABLE_ANCHOR = re.compile(r"Province\s+District\s+Livestock", re.I)

_NUM = r"(-|[\d,]+(?:\.\d+)?)"
_PROVINCE_ROW = re.compile(
    rf"^({'|'.join(re.escape(p) for p in PROVINCES)})\s+{_NUM}\s+{_NUM}\s+{_NUM}\s+"
    rf"{_NUM}\s+{_NUM}\s+{_NUM}\s*$"
)
_GRAND_TOTAL_ROW = re.compile(
    rf"^Grand Total\s+{_NUM}\s+{_NUM}\s+{_NUM}\s+{_NUM}\s+{_NUM}\s+{_NUM}\s*$"
)
# District rows in the 24-hour houses/livestock table: name then four integers.
_DISTRICT_ROW = re.compile(r"^([A-Z][A-Za-z' \-]*[A-Za-z])\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$")

_REPORT_NO = re.compile(r"Situation Report No\.?\s*(\d+)", re.I)
_REPORT_DATE = re.compile(r"Dated:\s*(.+?)\s*$", re.M)

# Listing cards.
_CARD = re.compile(
    r'<a[^>]+href="(?P<url>[^"]+\.pdf)"[^>]*>(?P<body>.*?)</a>', re.S | re.I
)
_CARD_TITLE = re.compile(r'class="sr-card__title"[^>]*>(?P<title>.*?)</p>', re.S | re.I)
_CARD_DATE = re.compile(r'class="sr-card__date"[^>]*>(?P<date>.*?)</p>', re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")


class SitrepParseError(Exception):
    """The PDF was fetched but does not look like a situation report any more."""


class PdfSupportMissing(Exception):
    """`pdfplumber` is not installed. It is an optional extra, by design."""


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", fragment))).strip()


def _number(token: str) -> float:
    """A dash means zero here, not "missing" -- NDMA uses it for an empty cell."""
    token = token.strip()
    if token in {"-", "", "--"}:
        return 0.0
    return float(token.replace(",", ""))


def _row_values(match: re.Match[str], start: int) -> dict[str, Any]:
    roads, bridges, full, partial, total, livestock = (
        _number(match.group(i)) for i in range(start, start + 6)
    )
    return {
        "roads_km": round(roads, 2),
        "bridges": int(bridges),
        "houses_full": int(full),
        "houses_partial": int(partial),
        "houses_total": int(total),
        "livestock": int(livestock),
    }


# --------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------


def listing_url() -> str:
    settings = get_settings()
    return f"{settings.ndma_base}/sitreps"


def parse_listing(markup: str) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for card in _CARD.finditer(markup):
        body = card.group("body")
        title_match = _CARD_TITLE.search(body)
        if not title_match:
            continue
        title = _clean(title_match.group("title"))
        number = _REPORT_NO.search(title)
        if not number:
            continue
        date_match = _CARD_DATE.search(body)
        reports.append(
            {
                "number": int(number.group(1)),
                "title": title,
                "date": _clean(date_match.group("date")) if date_match else None,
                "url": html.unescape(card.group("url")),
            }
        )
    return reports


def latest_sitrep() -> dict[str, Any]:
    """Metadata for the newest published report. Cheap: the listing is ~36 KB."""
    url = listing_url()
    result = get_text(url, params={"cat_id": 3}, accept="text/html", timeout=20.0)
    if not result.ok:
        return {"available": False, "error": result.error, "source_url": url}

    reports = parse_listing(str(result.data))
    if not reports:
        return {
            "available": False,
            "error": (
                "no situation report cards found on the NDMA listing -- "
                "the page layout has probably changed"
            ),
            "source_url": url,
        }

    newest = max(reports, key=lambda r: int(r["number"]))
    return {"available": True, **newest, "source_url": url}


# --------------------------------------------------------------------------
# The PDF
# --------------------------------------------------------------------------


def _extract_text(payload: bytes) -> str:
    """Layout-aware text extraction. Seam: stubbed in tests, real in production.

    `pypdf`'s naive extraction mangles these tables -- the glyph runs are
    fragmented -- so pdfplumber is required rather than merely preferred.
    """
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - exercised via the stub
        raise PdfSupportMissing("pdfplumber is not installed") from exc

    import io

    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n".join(pages)


def parse_sitrep(text: str) -> dict[str, Any]:
    """Pull the province and district tables out of the report text.

    Raises `SitrepParseError` when the province table anchor is absent, which is
    how a layout change surfaces instead of quietly producing nothing.
    """
    anchor = PROVINCE_TABLE_ANCHOR.search(text)
    if not anchor:
        raise SitrepParseError(
            "the cumulative province table was not found in the report text"
        )

    # Scoped to the anchored table, and stopped at its own Grand Total.
    #
    # This is not defensive tidiness. Report 69 carries a *second* province
    # table later in the document (relief items by province) whose rows have the
    # identical six-number shape -- "KP 0 0 0 0 192 1298". Scanning the whole
    # document let that row overwrite the real one, and the console showed 192
    # houses damaged in KP where the report said 370. The figures looked
    # entirely plausible, which is what made it dangerous.
    provinces: dict[str, dict[str, Any]] = {}
    grand_total: dict[str, Any] | None = None
    for raw in text[anchor.end() :].splitlines():
        line = raw.strip()
        row = _PROVINCE_ROW.match(line)
        if row:
            provinces[row.group(1)] = _row_values(row, 2)
            continue
        total = _GRAND_TOTAL_ROW.match(line)
        if total:
            grand_total = _row_values(total, 1)
            break

    if not provinces:
        raise SitrepParseError(
            "the cumulative province table header was present but no province "
            "rows matched"
        )

    districts = _parse_districts(text)

    number = _REPORT_NO.search(text)
    date = _REPORT_DATE.search(text)
    return {
        "report_number": int(number.group(1)) if number else None,
        "report_date": date.group(1).strip() if date else None,
        "provinces": provinces,
        "grand_total": grand_total,
        "districts": districts,
    }


def _parse_districts(text: str) -> dict[str, dict[str, Any]]:
    """District rows from the 24-hour houses/livestock table.

    Scoped to the block following that table's own header: the same four-integer
    shape appears elsewhere in the document, and matching it document-wide would
    invent districts out of unrelated rows.
    """
    anchor = DISTRICT_TABLE_ANCHOR.search(text)
    if not anchor:
        return {}

    districts: dict[str, dict[str, Any]] = {}
    for raw in text[anchor.end() :].splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(("Total", "Grand Total")):
            break
        row = _DISTRICT_ROW.match(line)
        if not row:
            continue
        name = row.group(1).strip()
        # A stray province token can sit on its own line between district rows.
        if name in PROVINCES or name in {"Total", "Grand Total", "Partial Full Total"}:
            continue
        districts[name] = {
            "houses_partial": int(row.group(2)),
            "houses_full": int(row.group(3)),
            "houses_total": int(row.group(4)),
            "livestock": int(row.group(5)),
        }
    return districts


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


def situation(
    district: str = "Nowshera",
    province: str = "KP",
    *,
    table: Table | None = None,
) -> dict[str, Any]:
    """Figures from the newest NDMA sitrep, for one district and its province.

    Never raises. A district absent from the report is reported as
    `district_reported: false` -- that means "no incident in the last 24 hours",
    which is information, not a failure.
    """
    latest = latest_sitrep()
    if not latest.get("available"):
        return {
            "available": False,
            "error": latest.get("error"),
            "source_url": latest.get("source_url"),
        }

    number = latest["number"]
    store = table or get_table()
    key = ndma_pk(str(number))
    cached = store.get_body(key, CACHE_SK)

    meta = {
        "report_number": number,
        "report_date": latest.get("date"),
        "report_url": latest.get("url"),
        "source_url": latest.get("source_url"),
    }

    if cached:
        return _answer(cached, district, province, meta, cached=True)

    fetched = get_bytes(str(latest["url"]), timeout=120.0)
    if not fetched.ok:
        return {
            "available": False,
            **meta,
            "error": (
                f"NDMA Sitrep No. {number} ({latest.get('date')}) could not be "
                f"downloaded: {fetched.error}"
            ),
        }

    try:
        text = _extract_text(bytes(fetched.data or b""))
    except PdfSupportMissing as exc:
        return {
            "available": False,
            **meta,
            "error": str(exc),
            "remedy": "install the optional extra: uv sync --extra ndma",
        }

    try:
        parsed = parse_sitrep(text)
    except SitrepParseError as exc:
        # The chosen failure mode: name the report, link it, show nothing else.
        return {
            "available": False,
            **meta,
            "error": (
                f"NDMA Sitrep No. {number} ({latest.get('date')}) was fetched, "
                f"but the district table could not be parsed: {exc}"
            ),
        }

    store.put_model(key, CACHE_SK, parsed)
    return _answer(parsed, district, province, meta, cached=False)


def _answer(
    parsed: dict[str, Any],
    district: str,
    province: str,
    meta: dict[str, Any],
    *,
    cached: bool,
) -> dict[str, Any]:
    districts = parsed.get("districts") or {}
    match = next((v for k, v in districts.items() if k.casefold() == district.casefold()), None)
    return {
        "available": True,
        **meta,
        "cached": cached,
        "province_name": province,
        "province": (parsed.get("provinces") or {}).get(province),
        "grand_total": parsed.get("grand_total"),
        "district_name": district,
        "district": match,
        "district_reported": match is not None,
        "districts_reported": sorted(districts),
    }


@tool
def ndma_situation(district: str = "Nowshera", province: str = "KP") -> dict[str, Any]:
    """National flood damage figures for one district and its province.

    Reads the newest NDMA daily situation report. This is national reporting,
    published a day behind and aggregated to district level -- it says how bad
    the flood is in the area, never anything about an individual household.

    Args:
        district: District name as NDMA spells it, e.g. "Nowshera".
        province: Province abbreviation, e.g. "KP", "Sindh", "Punjab".

    Returns:
        Deaths, injuries and houses damaged for the district and the province,
        plus the report number and date. Returns `available: false` with a
        reason -- an unparseable report, a missing PDF library, an unreachable
        site -- rather than raising or falling back to an older report.
    """
    return situation(district=district, province=province)
