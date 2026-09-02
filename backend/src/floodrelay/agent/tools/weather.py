"""Rainfall and short-range forecast from Open-Meteo (no API key).

Feeds the triage node's explanation, not its arithmetic: "more rain due tonight"
is context a coordinator wants, but it does not move the urgency number, which
stays deterministic in scoring.py.
"""

from __future__ import annotations

from typing import Any

from strands import tool

from ...config import get_settings
from ._http import get_json


def rainfall_for(lat: float, lon: float) -> dict[str, Any]:
    s = get_settings()
    result = get_json(
        f"{s.open_meteo_base}/forecast",
        params={
            "latitude": round(lat, 3),
            "longitude": round(lon, 3),
            "hourly": "precipitation",
            "daily": "precipitation_sum",
            "past_days": 2,
            "forecast_days": 2,
            "timezone": "auto",
        },
        timeout=10.0,
    )
    if not result.ok:
        return {"available": False, "error": result.error}

    data = result.data or {}
    daily = data.get("daily", {}) or {}
    sums = [v for v in (daily.get("precipitation_sum") or []) if isinstance(v, int | float)]
    hourly = [v for v in ((data.get("hourly") or {}).get("precipitation") or [])
              if isinstance(v, int | float)]

    return {
        "available": True,
        "recent_48h_mm": round(sum(sums[:2]), 1) if sums else None,
        "next_48h_mm": round(sum(sums[2:4]), 1) if len(sums) > 2 else None,
        "max_hourly_mm": round(max(hourly), 1) if hourly else None,
        "units": "mm",
    }


@tool
def rainfall(lat: float, lon: float) -> dict[str, Any]:
    """Recent and forecast rainfall at a point, in millimetres.

    Args:
        lat: Latitude.
        lon: Longitude.

    Returns:
        A dict with `recent_48h_mm`, `next_48h_mm` and `max_hourly_mm`, or
        `available: false` with an `error` string if the service could not be
        reached. Never raises.
    """
    return rainfall_for(lat, lon)
