"""Global flood alerts from GDACS.

The one genuinely worldwide live alert feed in this project: every other source
here is either about Pakistan or is a global sensor cropped to one district.
GDACS answers a different question — "what else is happening, and how does this
compare" — and it is what tells a coordinator whether their district is a
footnote or the headline.

Keyless RSS. No registration, no key, no quota.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from floodrelay.agent.tools import gdacs

FIXTURES = Path(__file__).parent / "fixtures"
RSS_URL = "https://www.gdacs.org/xml/rss.xml"


@pytest.fixture
def feed() -> str:
    return (FIXTURES / "gdacs_rss.xml").read_text(encoding="utf-8")


def mock_feed(body: str) -> None:
    respx.get(RSS_URL).mock(return_value=httpx.Response(200, text=body))


def find(result: dict[str, object], iso3: str) -> dict[str, object]:
    alerts = result["alerts"]
    assert isinstance(alerts, list)
    match = [a for a in alerts if a["iso3"] == iso3]
    assert match, f"{iso3} missing from {[a['iso3'] for a in alerts]}"
    return match[0]  # type: ignore[no-any-return]


@respx.mock
def test_parses_a_flood_alert(feed: str) -> None:
    mock_feed(feed)

    pakistan = find(gdacs.flood_alerts(), "PAK")

    assert pakistan["level"] == "Green"
    assert pakistan["country"] == "Pakistan"
    assert pakistan["event_id"] == "1104136"
    assert "gdacs.org" in str(pakistan["url"])
    assert "deaths" in str(pakistan["summary"])


@respx.mock
def test_alert_level_is_read_from_the_element_not_the_title(feed: str) -> None:
    """GDACS's own feed disagrees with itself.

    The Nepal item is titled "Orange flood alert in Nepal" while its
    `gdacs:alertlevel` element says `Red`. Parsing the human-readable title —
    the obvious shortcut — would under-report a red alert as orange. The
    machine-readable field wins.
    """
    mock_feed(feed)

    nepal = find(gdacs.flood_alerts(), "NPL")

    assert nepal["level"] == "Red"
    assert "Orange" in str(nepal["title"])


@respx.mock
def test_non_flood_events_are_excluded(feed: str) -> None:
    """The feed is 382 items and mostly wildfires. Only floods belong here."""
    mock_feed(feed)

    result = gdacs.flood_alerts()

    assert all(a["event_type"] == "FL" for a in result["alerts"])  # type: ignore[union-attr]
    assert not any("earthquake" in str(a["title"]).lower() for a in result["alerts"])  # type: ignore[union-attr]


@respx.mock
def test_counts_are_grouped_by_severity(feed: str) -> None:
    mock_feed(feed)

    counts = gdacs.flood_alerts()["counts"]

    assert counts == {"Red": 1, "Orange": 1, "Green": 1}


@respx.mock
def test_alerts_for_the_configured_country_are_called_out(feed: str) -> None:
    """The coordinator's own country should not have to be hunted for in a
    global list."""
    mock_feed(feed)

    result = gdacs.flood_alerts(country="Pakistan")

    assert [a["iso3"] for a in result["here"]] == ["PAK"]  # type: ignore[union-attr]


@respx.mock
def test_country_matching_accepts_case_and_iso3(feed: str) -> None:
    mock_feed(feed)

    assert len(gdacs.flood_alerts(country="pakistan")["here"]) == 1  # type: ignore[arg-type]
    assert len(gdacs.flood_alerts(country="PAK")["here"]) == 1  # type: ignore[arg-type]


@respx.mock
def test_a_country_with_no_alert_is_reported_as_none_not_an_error(feed: str) -> None:
    """No alert for a country is good news, and must read as good news rather
    than as a broken feed."""
    mock_feed(feed)

    result = gdacs.flood_alerts(country="Bhutan")

    assert result["available"] is True
    assert result["here"] == []


@respx.mock
def test_a_feed_with_no_floods_is_not_a_failure() -> None:
    mock_feed(
        '<?xml version="1.0"?><rss version="2.0" xmlns:gdacs="http://www.gdacs.org">'
        "<channel><title>GDACS</title></channel></rss>"
    )

    result = gdacs.flood_alerts()

    assert result["available"] is True
    assert result["alerts"] == []
    assert result["counts"] == {"Red": 0, "Orange": 0, "Green": 0}


@respx.mock
def test_unreachable_service_returns_a_value_not_an_exception() -> None:
    respx.get(RSS_URL).mock(side_effect=httpx.ConnectError("no route"))

    result = gdacs.flood_alerts()

    assert result["available"] is False
    assert result["alerts"] == []
    assert result["error"]


@respx.mock
def test_malformed_xml_is_reported_rather_than_crashing() -> None:
    mock_feed("<rss>truncated")

    result = gdacs.flood_alerts()

    assert result["available"] is False
    assert result["error"]


@respx.mock
def test_alerts_are_ordered_most_severe_first(feed: str) -> None:
    """A red alert below a green one in a scanned list is a red alert missed."""
    mock_feed(feed)

    levels = [a["level"] for a in gdacs.flood_alerts()["alerts"]]  # type: ignore[union-attr]

    assert levels == ["Red", "Orange", "Green"]


@respx.mock
def test_limit_is_applied_after_ordering(feed: str) -> None:
    """Truncating before sorting would drop the severe ones first."""
    mock_feed(feed)

    alerts = gdacs.flood_alerts(limit=1)["alerts"]

    assert len(alerts) == 1  # type: ignore[arg-type]
    assert alerts[0]["level"] == "Red"  # type: ignore[index]


@respx.mock
def test_requests_an_accept_header_gdacs_will_answer(feed: str) -> None:
    """GDACS answers 406 Not Acceptable to `Accept: text/xml`.

    Found against the live service: the shared `get_text` default was rejected
    outright while curl's `*/*` was served. The header is asserted here because
    the failure is invisible from the fixture -- every mocked test passed while
    the real feed returned nothing.
    """
    route = respx.get(RSS_URL).mock(return_value=httpx.Response(200, text=feed))

    gdacs.flood_alerts()

    accept = route.calls[0].request.headers["accept"]
    assert accept != "text/xml"
    assert "*/*" in accept


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------
#
# The feed is 1.1 MB and took 15.8 seconds against the live service, which made
# it 15.8 of /context's 21.4 seconds. Every panel load and every fifteen-minute
# refetch, in every open tab, was pulling all of it. That is slow for the
# coordinator and rude to GDACS, who serve this for free.


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    gdacs.reset_cache()


@respx.mock
def test_a_second_call_is_served_from_cache(feed: str) -> None:
    route = respx.get(RSS_URL).mock(return_value=httpx.Response(200, text=feed))

    first = gdacs.flood_alerts()
    second = gdacs.flood_alerts()

    assert route.call_count == 1
    assert second["cached"] is True
    assert first["alerts"] == second["alerts"]


@respx.mock
def test_an_expired_cache_is_refetched(feed: str) -> None:
    route = respx.get(RSS_URL).mock(return_value=httpx.Response(200, text=feed))

    gdacs.flood_alerts()
    gdacs.flood_alerts(max_age_s=0)

    assert route.call_count == 2


@respx.mock
def test_a_failure_is_not_cached(feed: str) -> None:
    """A transient outage must not lock the panel into an error for the whole
    TTL -- the next caller should get a real attempt."""
    respx.get(RSS_URL).mock(side_effect=httpx.ConnectError("no route"))
    assert gdacs.flood_alerts()["available"] is False

    # Re-mocking the same pattern reuses the route object, so its call_count
    # still carries the failed attempt. What matters is that the second call
    # went out at all and came back good, rather than being served a cached
    # error for the next fifteen minutes.
    respx.get(RSS_URL).mock(return_value=httpx.Response(200, text=feed))
    second = gdacs.flood_alerts()
    assert second["available"] is True
    assert second["cached"] is False


@respx.mock
def test_a_different_country_is_answered_from_the_same_cached_feed(feed: str) -> None:
    """The feed is global; the country only selects from it. Re-downloading
    1.1 MB to answer a different country would be absurd."""
    route = respx.get(RSS_URL).mock(return_value=httpx.Response(200, text=feed))

    gdacs.flood_alerts(country="Pakistan")
    nepal = gdacs.flood_alerts(country="Nepal")

    assert route.call_count == 1
    assert [a["iso3"] for a in nepal["here"]] == ["NPL"]  # type: ignore[union-attr]
