"""Situation context from ReliefWeb.

Background for the coordinator, not an input to any score. Headlines about the
wider response help a human judge whether a district is already being covered;
they are never allowed to move a priority number.

Two paths, and the fallback is the one that works today
-------------------------------------------------------
ReliefWeb v1 was decommissioned and answers 410 Gone. v2 replaces it but
rejects unapproved appnames with a 403, and approval is a manual form reviewed
by email -- not something a running console can wait on.

So: with `RELIEFWEB_APPNAME` set, the JSON API is used. With it unset, the
keyless RSS feed is used instead. The result says which path produced it, so
nobody has to guess whether they are seeing the good data or the fallback.

Registering an appname is worth doing -- the API carries structured dates and
sources that RSS does not -- but nothing here depends on it happening.
"""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree

from strands import tool

from ...config import get_settings
from ._http import get_json, get_text


def _from_api(country: str, limit: int, appname: str) -> dict[str, Any]:
    settings = get_settings()
    url = f"{settings.reliefweb_base}/reports"
    result = get_json(
        url,
        params={
            "appname": appname,
            "query[value]": f"{country} flood",
            "query[operator]": "AND",
            "limit": limit,
            "sort[]": "date:desc",
            "fields[include][]": ["title", "date.created", "source.shortname"],
            "profile": "list",
        },
        timeout=12.0,
    )
    if not result.ok:
        return {"available": False, "source": "api", "error": result.error, "reports": []}

    data = result.data if isinstance(result.data, dict) else {}
    reports = []
    for item in data.get("data", []) or []:
        fields = item.get("fields") or {}
        sources = fields.get("source") or []
        reports.append(
            {
                "title": fields.get("title"),
                "url": item.get("href"),
                "date": (fields.get("date") or {}).get("created"),
                "source": ", ".join(
                    s.get("shortname", "") for s in sources if isinstance(s, dict)
                )
                or None,
            }
        )
    return {"available": True, "source": "api", "reports": reports, "source_url": url}


def _from_rss(country: str, limit: int) -> dict[str, Any]:
    settings = get_settings()
    url = settings.reliefweb_rss
    result = get_text(
        url, params={"search": f"{country} flood situation report"}, timeout=15.0
    )
    if not result.ok:
        return {"available": False, "source": "rss", "error": result.error, "reports": []}

    try:
        root = ElementTree.fromstring(str(result.data))
    except ElementTree.ParseError as exc:
        return {
            "available": False,
            "source": "rss",
            "error": f"ReliefWeb RSS was not valid XML: {exc}",
            "reports": [],
        }

    reports = []
    for item in root.iter("item"):
        title = item.findtext("title")
        if not title:
            continue
        reports.append(
            {
                "title": title.strip(),
                "url": (item.findtext("link") or "").strip() or None,
                "date": (item.findtext("pubDate") or "").strip() or None,
                "source": None,
            }
        )
        if len(reports) >= limit:
            break

    return {"available": True, "source": "rss", "reports": reports, "source_url": url}


def context_for(country: str = "Pakistan", days: int = 14, limit: int = 5) -> dict[str, Any]:
    """Recent ReliefWeb reports for a country. Never raises."""
    appname = get_settings().reliefweb_appname
    result = _from_api(country, limit, appname) if appname else _from_rss(country, limit)
    return {**result, "country": country, "days": days}


@tool
def situation_context(country: str = "Pakistan", days: int = 14) -> dict[str, Any]:
    """Recent ReliefWeb situation reports for a country, newest first.

    Background only. This must not influence any urgency score; it exists so the
    coordinator can see what the wider response already knows.

    Args:
        country: Country name, e.g. "Pakistan".
        days: How far back to look, in days.

    Returns:
        A dict with `reports` (title, url, date) and a `source` of "api" or
        "rss" saying which path answered. Returns `available: false` with an
        `error` rather than raising if ReliefWeb cannot be reached.
    """
    return context_for(country, days)
