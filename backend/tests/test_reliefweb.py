"""Situation context from ReliefWeb.

This module existed and was silently dead. `api.reliefweb.int/v1` was
decommissioned and now answers 410 Gone, so `situation_context()` returned
`available: false` on every call while looking perfectly healthy in the code.

v2 is not a drop-in replacement: it rejects unapproved appnames with a 403, and
approval is a manual form reviewed by email. So the tool takes the keyless RSS
feed when no appname is configured, and the JSON API when one is. Both paths are
tested, because shipping only the path we cannot use today is how the module
died the first time.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
import respx

from floodrelay.agent.tools import reliefweb
from floodrelay.config import get_settings

API_URL = "https://api.reliefweb.int/v2/reports"
RSS_URL = "https://reliefweb.int/updates/rss.xml"

API_BODY = {
    "data": [
        {
            "href": "https://reliefweb.int/report/pakistan/pdma-kp-sitrep-1",
            "fields": {
                "title": "PDMA Khyber Pakhtunkhwa Daily Situation Report",
                "date": {"created": "2026-09-02T06:00:00+00:00"},
                "source": [{"shortname": "PDMA"}],
            },
        }
    ]
}

RSS_BODY = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <title>ReliefWeb - Updates</title>
  <item>
    <title>PDMA Khyber Pakhtunkhwa Daily Situation Report</title>
    <link>https://reliefweb.int/report/pakistan/pdma-kp-sitrep-1</link>
    <pubDate>Wed, 02 Sep 2026 06:00:00 +0000</pubDate>
  </item>
  <item>
    <title>Pakistan: Monsoon Floods Flash Update</title>
    <link>https://reliefweb.int/report/pakistan/flash-update-4</link>
    <pubDate>Mon, 01 Sep 2026 09:00:00 +0000</pubDate>
  </item>
</channel></rss>
"""


@pytest.fixture
def no_appname(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("RELIEFWEB_APPNAME", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def with_appname(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("RELIEFWEB_APPNAME", "floodrelay-demo-a1b2c3")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_default_base_is_not_the_decommissioned_v1(no_appname: None) -> None:
    """Regression guard. v1 answers 410 Gone; pointing at it is a silent outage."""
    assert "/v1" not in get_settings().reliefweb_base
    assert get_settings().reliefweb_base.endswith("/v2")


@respx.mock
def test_uses_the_json_api_when_an_appname_is_configured(with_appname: None) -> None:
    respx.get(API_URL).mock(return_value=httpx.Response(200, json=API_BODY))

    result = reliefweb.context_for("Pakistan")

    assert result["available"] is True
    assert result["source"] == "api"
    assert result["reports"][0]["title"] == (
        "PDMA Khyber Pakhtunkhwa Daily Situation Report"
    )
    assert result["reports"][0]["url"].startswith("https://reliefweb.int/report")


@respx.mock
def test_falls_back_to_rss_when_no_appname_is_configured(no_appname: None) -> None:
    """The whole point: this works today, with nothing registered."""
    api = respx.get(API_URL).mock(return_value=httpx.Response(403))
    rss = respx.get(RSS_URL).mock(return_value=httpx.Response(200, text=RSS_BODY))

    result = reliefweb.context_for("Pakistan")

    assert result["available"] is True
    assert result["source"] == "rss"
    assert api.call_count == 0, "must not spend a request on an API it cannot use"
    assert rss.call_count == 1
    assert len(result["reports"]) == 2


@respx.mock
def test_rss_reports_carry_titles_and_links(no_appname: None) -> None:
    respx.get(RSS_URL).mock(return_value=httpx.Response(200, text=RSS_BODY))

    reports = reliefweb.context_for("Pakistan")["reports"]

    assert reports[1]["title"] == "Pakistan: Monsoon Floods Flash Update"
    assert reports[1]["url"] == "https://reliefweb.int/report/pakistan/flash-update-4"


@respx.mock
def test_unapproved_appname_says_so_instead_of_going_quiet(with_appname: None) -> None:
    """A 403 here means the appname was rejected. The operator needs that word."""
    respx.get(API_URL).mock(
        return_value=httpx.Response(
            403, json={"error": {"message": "You are not using an approved appname."}}
        )
    )

    result = reliefweb.context_for("Pakistan")

    assert result["available"] is False
    assert "appname" in str(result["error"]).lower()


@respx.mock
def test_decommissioned_api_version_is_named_in_the_error(with_appname: None) -> None:
    """If a future version is retired the same way, say which, not just 'HTTP 410'."""
    respx.get(API_URL).mock(
        return_value=httpx.Response(
            410, json={"error": {"message": "The API version 'v2' has been decommissioned."}}
        )
    )

    result = reliefweb.context_for("Pakistan")

    assert result["available"] is False
    assert "decommissioned" in str(result["error"]).lower()


@respx.mock
def test_unreachable_service_returns_a_value_not_an_exception(no_appname: None) -> None:
    respx.get(RSS_URL).mock(side_effect=httpx.ConnectError("no route"))

    result = reliefweb.context_for("Pakistan")

    assert result["available"] is False
    assert result["reports"] == []
    assert result["error"]


@respx.mock
def test_malformed_rss_is_reported_rather_than_crashing(no_appname: None) -> None:
    respx.get(RSS_URL).mock(return_value=httpx.Response(200, text="<rss>truncated"))

    result = reliefweb.context_for("Pakistan")

    assert result["available"] is False
    assert result["error"]


@respx.mock
def test_situation_context_tool_never_raises(no_appname: None) -> None:
    """The @tool wrapper is what the agent loop touches. It must be total."""
    respx.get(RSS_URL).mock(side_effect=httpx.ConnectError("no route"))

    assert reliefweb.situation_context("Pakistan")["available"] is False
