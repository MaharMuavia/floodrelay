"""Global flood alerts from GDACS (Global Disaster Alert and Coordination System).

Run jointly by the European Commission and the UN. It is the only genuinely
worldwide live source in this project: everything else here is either about
Pakistan specifically, or a global sensor cropped to one district.

What it is for
--------------
Two questions a coordinator cannot answer from local data alone:

* Is my district's flood being tracked internationally at all, and at what
  severity?
* Is this the headline event or a footnote? Where a relief effort sits in the
  global queue shapes what outside help is plausible.

Keyless RSS -- no registration, no key, no quota.

One parsing rule matters: **severity comes from `gdacs:alertlevel`, never from
the title.** GDACS's own feed disagrees with itself -- an item titled "Orange
flood alert in Nepal" carries `<gdacs:alertlevel>Red</gdacs:alertlevel>` -- and
reading the human-readable string would quietly under-report a red alert.

Like every other tool here: context only, no influence on any urgency score,
never raises.
"""

from __future__ import annotations

import re
import time
from typing import Any
from xml.etree import ElementTree

from strands import tool

from ...config import get_settings
from ._http import get_text

NS = {
    "gdacs": "http://www.gdacs.org",
    "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#",
}

FLOOD = "FL"

# Red first: an alert ordered below a less severe one is an alert missed.
LEVEL_ORDER = {"Red": 0, "Orange": 1, "Green": 2}
LEVELS = ("Red", "Orange", "Green")

_WS = re.compile(r"\s+")

# The feed is 1.1 MB and takes the better part of twenty seconds to pull. It is
# a worldwide alert list, so it is identical for every caller and every country
# -- caching it once for the process is the difference between a panel that
# appears immediately and one that blocks for twenty seconds on every tab.
# Fifteen minutes is well inside the rate at which GDACS revises alert levels.
CACHE_TTL_S = 15 * 60

_memo: str | None = None
_memo_at: float = 0.0


def reset_cache() -> None:
    """Drop the cached feed. Used by tests and by anything forcing a refresh."""
    global _memo, _memo_at
    _memo = None
    _memo_at = 0.0


def _text(item: ElementTree.Element, path: str) -> str:
    found = item.find(path, NS)
    return _WS.sub(" ", (found.text or "").strip()) if found is not None else ""


def _float(item: ElementTree.Element, path: str) -> float | None:
    raw = _text(item, path)
    try:
        return float(raw)
    except ValueError:
        return None


def _matches(alert: dict[str, Any], country: str) -> bool:
    """Country match on either the name or the ISO3 code, case-insensitively."""
    wanted = country.strip().casefold()
    return wanted in {
        str(alert.get("country", "")).casefold(),
        str(alert.get("iso3", "")).casefold(),
    }


def _parse(xml: str, country: str, limit: int) -> dict[str, Any]:
    root = ElementTree.fromstring(xml)

    alerts: list[dict[str, Any]] = []
    for item in root.iter("item"):
        if _text(item, "gdacs:eventtype") != FLOOD:
            continue
        alerts.append(
            {
                "event_type": FLOOD,
                # From the element, not the title. See the module docstring.
                "level": _text(item, "gdacs:alertlevel") or "Green",
                "country": _text(item, "gdacs:country"),
                "iso3": _text(item, "gdacs:iso3"),
                "title": _text(item, "title"),
                "summary": _text(item, "gdacs:population") or _text(item, "description"),
                "url": _text(item, "link"),
                "event_id": _text(item, "gdacs:eventid"),
                "from_date": _text(item, "gdacs:fromdate"),
                "to_date": _text(item, "gdacs:todate"),
                "lat": _float(item, "geo:Point/geo:lat"),
                "lon": _float(item, "geo:Point/geo:long"),
            }
        )

    counts = {level: sum(1 for a in alerts if a["level"] == level) for level in LEVELS}
    alerts.sort(key=lambda a: LEVEL_ORDER.get(str(a["level"]), 99))
    here = [a for a in alerts if _matches(a, country)]

    return {
        "available": True,
        # Sorted before truncating: taking the first N of an unsorted list would
        # drop the severe alerts first, which is exactly backwards.
        "alerts": alerts[:limit],
        "counts": counts,
        "here": here,
        "country": country,
        "total": len(alerts),
    }


def _failure(url: str, error: str | None) -> dict[str, Any]:
    return {
        "available": False,
        "error": error,
        "alerts": [],
        "counts": dict.fromkeys(LEVELS, 0),
        "here": [],
        "source_url": url,
        "cached": False,
    }


def flood_alerts(
    country: str = "Pakistan", limit: int = 20, *, max_age_s: float = CACHE_TTL_S
) -> dict[str, Any]:
    """Current global flood alerts, most severe first. Never raises.

    The country only selects from the feed; it never changes what is fetched, so
    one cached download answers every country.
    """
    global _memo, _memo_at

    url = get_settings().gdacs_rss
    cached = _memo is not None and (time.time() - _memo_at) < max_age_s

    if not cached:
        # GDACS answers 406 Not Acceptable to a bare `Accept: text/xml`, which
        # is the shared default. Verified against the live service: curl's
        # `*/*` is served and `text/xml` is refused outright.
        result = get_text(
            url,
            timeout=40.0,
            accept="application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        )
        if not result.ok:
            # Deliberately not cached: a transient outage must not lock the
            # panel into an error for the whole TTL.
            return _failure(url, result.error)
        _memo, _memo_at = str(result.data), time.time()

    try:
        parsed = _parse(str(_memo), country, limit)
    except ElementTree.ParseError as exc:
        reset_cache()
        return _failure(url, f"GDACS RSS was not valid XML: {exc}")

    return {**parsed, "source_url": url, "cached": cached, "fetched_at": _memo_at}


@tool
def global_flood_alerts(country: str = "Pakistan") -> dict[str, Any]:
    """Current worldwide flood alerts from GDACS, most severe first.

    Background only. This must not influence any urgency score; it exists so the
    coordinator can see how their district sits against the global picture.

    Args:
        country: Country name or ISO3 code to call out separately, e.g.
            "Pakistan" or "PAK".

    Returns:
        A dict with `alerts` (level, country, title, url, summary), `counts` by
        alert level, and `here` for the named country. Returns
        `available: false` with an `error` rather than raising if GDACS cannot
        be reached.
    """
    return flood_alerts(country)
