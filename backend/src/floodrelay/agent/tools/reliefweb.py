"""Situation context from ReliefWeb.

Background for the coordinator, not an input to any score. Headlines about the
wider response help a human judge whether a district is already being covered;
they are never allowed to move a priority number.
"""

from __future__ import annotations

from typing import Any

from strands import tool

from ...config import get_settings
from ._http import get_json


def context_for(country: str = "Pakistan", days: int = 14, limit: int = 5) -> dict[str, Any]:
    s = get_settings()
    result = get_json(
        f"{s.reliefweb_base}/reports",
        params={
            "appname": "floodrelay-demo",
            "query[value]": f"{country} flood",
            "query[operator]": "AND",
            "limit": limit,
            "sort[]": "date:desc",
            "fields[include][]": "title",
            "profile": "list",
        },
        timeout=12.0,
    )
    if not result.ok:
        return {"available": False, "error": result.error, "reports": []}

    reports = [
        {
            "title": (item.get("fields") or {}).get("title"),
            "url": item.get("href"),
        }
        for item in (result.data or {}).get("data", [])
    ]
    return {"available": True, "country": country, "days": days, "reports": reports}


@tool
def situation_context(country: str = "Pakistan", days: int = 14) -> dict[str, Any]:
    """Recent ReliefWeb situation reports for a country, newest first.

    Background only. This must not influence any urgency score; it exists so the
    coordinator can see what the wider response already knows.

    Args:
        country: Country name, e.g. "Pakistan".
        days: How far back to look, in days.

    Returns:
        A dict with `reports` (title and url). Returns `available: false` with an
        `error` rather than raising if ReliefWeb cannot be reached.
    """
    return context_for(country, days)
