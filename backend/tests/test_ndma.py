"""NDMA daily monsoon situation report.

This is the most fragile component in the project and it is treated as such.
NDMA publishes a PDF, on their own schedule, in a layout they can change without
telling anyone. Everything here is built around one rule: **when the parse
fails, say so with the report number and a link, and never emit a number that
merely looks plausible.**

The text fixture is the real extraction of Daily Situation Report No. 69
(02 September 2026), including its genuinely awkward artefacts -- a province
token stranded on its own line between district rows, dashes standing in for
zero, thousands separators in some columns and not others. A tidied-up fixture
would let a parser pass here and fail on the real thing.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from floodrelay.agent.tools import ndma
from floodrelay.store.table import Table

FIXTURES = Path(__file__).parent / "fixtures"
LISTING_URL = "https://ndma.gov.pk/sitreps"
# NDMA emits a double slash in these hrefs ("//storage/..."). respx normalises
# that away when matching a literal url, so the PDF route is matched by regex --
# and the double slash is preserved in the fixture because it is what NDMA sends.
PDF_69 = r"https://www\.ndma\.gov\.pk//?storage/sitrep/September2026/6a9809a2200bf\.pdf"


@pytest.fixture
def listing_html() -> str:
    return (FIXTURES / "ndma_sitrep_listing.html").read_text(encoding="utf-8")


@pytest.fixture
def sitrep_text() -> str:
    return (FIXTURES / "ndma_sitrep_69.txt").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _stub_extractor(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Replace the PDF text extraction so tests never carry a 3 MB binary.

    The seam is deliberate: `_extract_text` is the thin part that needs
    pdfplumber, `parse_sitrep` is the fragile part that needs the tests.
    """
    text = (FIXTURES / "ndma_sitrep_69.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(ndma, "_extract_text", lambda _payload: text)
    yield


# --------------------------------------------------------------------------
# The listing
# --------------------------------------------------------------------------


@respx.mock
def test_latest_sitrep_reads_number_date_and_url(listing_html: str) -> None:
    respx.get(LISTING_URL).mock(return_value=httpx.Response(200, text=listing_html))

    result = ndma.latest_sitrep()

    assert result["available"] is True
    assert result["number"] == 69
    assert result["date"] == "02 Sep 2026"
    assert result["url"].endswith("6a9809a2200bf.pdf")


@respx.mock
def test_latest_is_the_highest_numbered_not_merely_the_first(listing_html: str) -> None:
    """Card order is NDMA's choice, not a contract."""
    reordered = listing_html.replace("No. 69", "No. 67zz").replace(
        "No. 67", "No. 69"
    ).replace("No. 67zz", "No. 67")
    respx.get(LISTING_URL).mock(return_value=httpx.Response(200, text=reordered))

    assert ndma.latest_sitrep()["number"] == 69


@respx.mock
def test_unreachable_listing_returns_a_value_not_an_exception() -> None:
    respx.get(LISTING_URL).mock(side_effect=httpx.ConnectError("no route"))

    result = ndma.latest_sitrep()

    assert result["available"] is False
    assert result["error"]


@respx.mock
def test_listing_without_cards_is_reported_as_a_layout_change() -> None:
    respx.get(LISTING_URL).mock(
        return_value=httpx.Response(200, text="<html><body>Site under maintenance</body></html>")
    )

    result = ndma.latest_sitrep()

    assert result["available"] is False
    assert "no situation report" in str(result["error"]).lower()


# --------------------------------------------------------------------------
# The parser -- pure, and where the fragility lives
# --------------------------------------------------------------------------


def test_parses_the_provincial_cumulative_row_for_kp(sitrep_text: str) -> None:
    parsed = ndma.parse_sitrep(sitrep_text)

    kp = parsed["provinces"]["KP"]
    assert kp["roads_km"] == 4.12
    assert kp["bridges"] == 5
    assert kp["houses_full"] == 99
    assert kp["houses_partial"] == 271
    assert kp["houses_total"] == 370
    assert kp["livestock"] == 289


def test_dashes_are_read_as_zero_not_dropped(sitrep_text: str) -> None:
    """Balochistan reports "- -" for roads and bridges. That is zero, and the
    row must still parse rather than being skipped for not matching."""
    balochistan = ndma.parse_sitrep(sitrep_text)["provinces"]["Balochistan"]

    assert balochistan["roads_km"] == 0.0
    assert balochistan["bridges"] == 0
    assert balochistan["houses_total"] == 278


def test_parses_the_grand_total(sitrep_text: str) -> None:
    total = ndma.parse_sitrep(sitrep_text)["grand_total"]

    assert total["houses_total"] == 1706
    assert total["livestock"] == 1483


def test_report_number_and_date_come_from_the_document(sitrep_text: str) -> None:
    parsed = ndma.parse_sitrep(sitrep_text)

    assert parsed["report_number"] == 69
    assert parsed["report_date"] == "2 September 2026"


def test_district_with_an_incident_is_parsed(sitrep_text: str) -> None:
    swabi = ndma.parse_sitrep(sitrep_text)["districts"]["Swabi"]

    assert swabi["houses_partial"] == 1
    assert swabi["houses_full"] == 0
    assert swabi["houses_total"] == 1
    assert swabi["livestock"] == 0


def test_all_districts_in_the_24h_table_are_found(sitrep_text: str) -> None:
    districts = ndma.parse_sitrep(sitrep_text)["districts"]

    assert set(districts) == {"Mardan", "Mohmand", "Swabi", "Buner"}


def test_total_rows_are_not_mistaken_for_districts(sitrep_text: str) -> None:
    """"Total" and "Grand Total" sit in the same column shape as a district."""
    districts = ndma.parse_sitrep(sitrep_text)["districts"]

    assert "Total" not in districts
    assert "Grand Total" not in districts


def test_text_without_the_province_table_is_a_parse_failure() -> None:
    """The province table is the anchor. Without it there is nothing to trust."""
    with pytest.raises(ndma.SitrepParseError):
        ndma.parse_sitrep("MOST IMMEDIATE\nSubject: something else entirely\n")


# --------------------------------------------------------------------------
# Putting it together
# --------------------------------------------------------------------------


@respx.mock
def test_situation_reports_a_district_that_appears(
    table: Table, listing_html: str
) -> None:
    respx.get(LISTING_URL).mock(return_value=httpx.Response(200, text=listing_html))
    respx.get(url__regex=PDF_69).mock(return_value=httpx.Response(200, content=b"%PDF-1.7 fake"))

    result = ndma.situation(district="Swabi", table=table)

    assert result["available"] is True
    assert result["district_reported"] is True
    assert result["district"]["houses_total"] == 1
    assert result["report_number"] == 69


@respx.mock
def test_district_absent_from_the_report_is_not_a_failure(
    table: Table, listing_html: str
) -> None:
    """Nowshera is not in report 69. That means "no incident reported in the
    last 24 hours", which is information -- not a broken parse."""
    respx.get(LISTING_URL).mock(return_value=httpx.Response(200, text=listing_html))
    respx.get(url__regex=PDF_69).mock(return_value=httpx.Response(200, content=b"%PDF-1.7 fake"))

    result = ndma.situation(district="Nowshera", table=table)

    assert result["available"] is True
    assert result["district_reported"] is False
    assert result["district"] is None
    assert result["province"]["houses_total"] == 370


@respx.mock
def test_pdf_is_not_refetched_while_the_report_number_is_unchanged(
    table: Table, listing_html: str
) -> None:
    """Steady state is one 3 MB fetch a day. Scraping a government site is a
    courtesy relationship and that budget is what keeps it defensible."""
    respx.get(LISTING_URL).mock(return_value=httpx.Response(200, text=listing_html))
    pdf = respx.get(url__regex=PDF_69).mock(
        return_value=httpx.Response(200, content=b"%PDF-1.7 fake")
    )

    ndma.situation(district="Nowshera", table=table)
    second = ndma.situation(district="Nowshera", table=table)

    assert pdf.call_count == 1
    assert second["cached"] is True


@respx.mock
def test_a_new_report_number_triggers_a_refetch(
    table: Table, listing_html: str
) -> None:
    respx.get(LISTING_URL).mock(return_value=httpx.Response(200, text=listing_html))
    pdf = respx.get(url__regex=PDF_69).mock(
        return_value=httpx.Response(200, content=b"%PDF-1.7 fake")
    )
    ndma.situation(district="Nowshera", table=table)

    bumped = listing_html.replace("No. 69", "No. 70")
    respx.get(LISTING_URL).mock(return_value=httpx.Response(200, text=bumped))
    ndma.situation(district="Nowshera", table=table)

    assert pdf.call_count == 2


@respx.mock
def test_fetched_but_unparseable_names_the_report_and_links_the_pdf(
    table: Table, listing_html: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chosen failure mode, and the reason this module is sequenced last.

    NDMA will reformat the sitrep eventually. When they do, the console must say
    which report it could not read -- not fall back to yesterday, and not render
    a plausible-looking zero.
    """
    monkeypatch.setattr(ndma, "_extract_text", lambda _payload: "totally different layout")
    respx.get(LISTING_URL).mock(return_value=httpx.Response(200, text=listing_html))
    respx.get(url__regex=PDF_69).mock(return_value=httpx.Response(200, content=b"%PDF-1.7 fake"))

    result = ndma.situation(district="Nowshera", table=table)

    assert result["available"] is False
    assert result["report_number"] == 69
    assert result["report_date"] == "02 Sep 2026"
    assert result["report_url"].endswith(".pdf")
    error = str(result["error"])
    assert "69" in error and "could not be parsed" in error.lower()


@respx.mock
def test_unreachable_pdf_names_the_report_it_failed_on(
    table: Table, listing_html: str
) -> None:
    respx.get(LISTING_URL).mock(return_value=httpx.Response(200, text=listing_html))
    respx.get(url__regex=PDF_69).mock(side_effect=httpx.ConnectError("no route"))

    result = ndma.situation(district="Nowshera", table=table)

    assert result["available"] is False
    assert "69" in str(result["error"])


@respx.mock
def test_missing_pdfplumber_is_reported_honestly(
    table: Table, listing_html: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`pdfplumber` is an optional extra so the base install stays light. When
    it is absent the tool says exactly that instead of failing obscurely."""

    def _no_lib(_payload: bytes) -> str:
        raise ndma.PdfSupportMissing("pdfplumber is not installed")

    monkeypatch.setattr(ndma, "_extract_text", _no_lib)
    respx.get(LISTING_URL).mock(return_value=httpx.Response(200, text=listing_html))
    respx.get(url__regex=PDF_69).mock(return_value=httpx.Response(200, content=b"%PDF-1.7 fake"))

    result = ndma.situation(district="Nowshera", table=table)

    assert result["available"] is False
    assert "pdfplumber" in str(result["error"])
    assert "uv sync --extra ndma" in str(result["remedy"])


@respx.mock
def test_situation_never_raises_when_everything_is_down(table: Table) -> None:
    respx.get(LISTING_URL).mock(side_effect=httpx.ConnectError("no route"))

    result: dict[str, Any] = ndma.situation(district="Nowshera", table=table)

    assert result["available"] is False
    assert result["error"]


def test_a_second_province_table_does_not_overwrite_the_first(sitrep_text: str) -> None:
    """Report 69 carries two tables with identical six-number province rows.

    The relief-items table later in the document has a KP row reading
    "KP 0 0 0 0 192 1298". A document-wide scan let it win, and the console
    reported 192 houses damaged in KP where the report said 370 -- a wrong
    number that looked entirely reasonable. Found by running against the live
    PDF, not by the original fixture, which was too small to contain the clash.
    """
    provinces = ndma.parse_sitrep(sitrep_text)["provinces"]

    assert provinces["KP"]["houses_total"] == 370
    assert provinces["KP"]["livestock"] == 289
    assert provinces["Sindh"]["houses_total"] == 40
