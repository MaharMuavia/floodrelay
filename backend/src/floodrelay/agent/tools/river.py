"""River discharge from Open-Meteo's GloFAS endpoint (no API key).

Sits beside `weather.py` and honours the same contract. Rainfall says how much
water is arriving; discharge says how much is already moving down the channel.
For a district on a river -- Nowshera is on the Kabul -- the second is often the
number that decides whether tonight is worse than this afternoon.

It feeds the coordinator's judgement and the triage node's explanation. It does
**not** feed the urgency arithmetic, which stays deterministic in scoring.py.
"""

from __future__ import annotations

from typing import Any

from strands import tool

from ...config import get_settings
from ._http import get_json

# We ask for seven days back and seven forward, so today sits at index 7.
# Deriving the index from our own request parameters keeps the reading
# independent of the host clock.
PAST_DAYS = 7
FORECAST_DAYS = 7

# Below this the movement is model noise, not a trend worth reporting.
TREND_BAND = 0.05


def _trend(current: float, forward: list[float]) -> str:
    """Direction over the next three days, or "unknown" if there is no forecast."""
    window = forward[:3]
    if not window or current <= 0:
        return "unknown"
    ratio = (sum(window) / len(window)) / current
    if ratio >= 1 + TREND_BAND:
        return "rising"
    if ratio <= 1 - TREND_BAND:
        return "falling"
    return "steady"


def discharge_for(lat: float, lon: float) -> dict[str, Any]:
    """Current and forecast river discharge at a point. Never raises."""
    settings = get_settings()
    url = f"{settings.open_meteo_flood_base}/flood"

    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return {
            "available": False,
            "error": f"coordinate out of range: {lat},{lon}",
            "current_m3s": None,
            "source_url": url,
        }

    result = get_json(
        url,
        params={
            "latitude": round(lat, 3),
            "longitude": round(lon, 3),
            "daily": "river_discharge,river_discharge_max",
            "past_days": PAST_DAYS,
            "forecast_days": FORECAST_DAYS,
        },
        timeout=12.0,
    )
    if not result.ok:
        return {
            "available": False,
            "error": result.error,
            "current_m3s": None,
            "source_url": url,
        }

    daily = ((result.data or {}).get("daily") or {}) if isinstance(result.data, dict) else {}
    discharge = list(daily.get("river_discharge") or [])
    dates = list(daily.get("time") or [])

    current = discharge[PAST_DAYS] if len(discharge) > PAST_DAYS else None
    if not isinstance(current, int | float):
        # A null today is a real answer -- the model has no value here -- and it
        # must not be smoothed into a number that looks measured.
        return {
            "available": False,
            "error": "no discharge value published for today at this point",
            "current_m3s": None,
            "source_url": url,
        }

    forward = [v for v in discharge[PAST_DAYS + 1 :] if isinstance(v, int | float)]

    return {
        "available": True,
        "current_m3s": round(float(current), 1),
        "max_next_7d_m3s": round(max(forward), 1) if forward else None,
        "trend": _trend(float(current), [float(v) for v in forward]),
        "as_of": dates[PAST_DAYS] if len(dates) > PAST_DAYS else None,
        "units": "m³/s",
        "model": "GloFAS v4 via Open-Meteo",
        "source_url": url,
    }


@tool
def river_discharge(lat: float, lon: float) -> dict[str, Any]:
    """Current and forecast river discharge at a point, in cubic metres per second.

    Background for a human decision. This must not influence any urgency score.

    Args:
        lat: Latitude.
        lon: Longitude.

    Returns:
        A dict with `current_m3s`, `max_next_7d_m3s` and a `trend` of
        rising/falling/steady/unknown, or `available: false` with an `error`
        string if the service could not be reached. Never raises.
    """
    return discharge_for(lat, lon)
