"""River discharge from the Open-Meteo GloFAS endpoint.

Context for the coordinator, never arithmetic. "The river is still rising" is
something a person should know while deciding; it is not something that may
quietly move a priority number. `test_scoring` holds that line; these tests
cover the reading itself.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from floodrelay.agent.tools import river

FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"

# Shape copied from a live response for 34.015,71.975 (the Kabul river at
# Nowshera) on 2026-09-03: past_days=7 + forecast_days=7, so index 7 is today.
DATES = [
    "2026-08-27", "2026-08-28", "2026-08-29", "2026-08-30", "2026-08-31",
    "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05",
    "2026-09-06", "2026-09-07", "2026-09-08", "2026-09-09",
]


def payload(discharge: list[float | None]) -> dict[str, Any]:
    return {
        "latitude": 34.025,
        "longitude": 71.975006,
        "daily_units": {"time": "iso8601", "river_discharge": "m³/s"},
        "daily": {
            "time": DATES,
            "river_discharge": discharge,
            "river_discharge_max": discharge,
        },
    }


def series(today: float, after: list[float]) -> list[float | None]:
    """Seven past days, today, then the forecast, padded to 14 entries."""
    values: list[float | None] = [800.0] * 7 + [today] + list(after)
    return (values + [None] * 14)[:14]


@respx.mock
def test_reports_todays_discharge() -> None:
    respx.get(FLOOD_URL).mock(
        return_value=httpx.Response(200, json=payload(series(834.6, [830.0, 828.0, 825.0])))
    )

    result = river.discharge_for(34.015, 71.975)

    assert result["available"] is True
    assert result["current_m3s"] == 834.6
    assert result["as_of"] == "2026-09-03"
    assert result["units"] == "m³/s"


@respx.mock
def test_trend_is_rising_when_the_forecast_climbs() -> None:
    respx.get(FLOOD_URL).mock(
        return_value=httpx.Response(200, json=payload(series(800.0, [900.0, 950.0, 1000.0])))
    )

    assert river.discharge_for(34.015, 71.975)["trend"] == "rising"


@respx.mock
def test_trend_is_falling_when_the_forecast_drops() -> None:
    respx.get(FLOOD_URL).mock(
        return_value=httpx.Response(200, json=payload(series(800.0, [700.0, 650.0, 600.0])))
    )

    assert river.discharge_for(34.015, 71.975)["trend"] == "falling"


@respx.mock
def test_small_movement_is_steady_not_a_trend() -> None:
    """A 1% wobble is noise. Calling it 'rising' would cry wolf."""
    respx.get(FLOOD_URL).mock(
        return_value=httpx.Response(200, json=payload(series(800.0, [804.0, 798.0, 802.0])))
    )

    assert river.discharge_for(34.015, 71.975)["trend"] == "steady"


@respx.mock
def test_peak_of_the_forecast_window_is_reported() -> None:
    respx.get(FLOOD_URL).mock(
        return_value=httpx.Response(
            200, json=payload(series(800.0, [900.0, 1200.0, 950.0, 880.0]))
        )
    )

    result = river.discharge_for(34.015, 71.975)

    assert result["max_next_7d_m3s"] == 1200.0


@respx.mock
def test_unreachable_service_returns_a_value_not_an_exception() -> None:
    respx.get(FLOOD_URL).mock(side_effect=httpx.ConnectError("no route"))

    result = river.discharge_for(34.015, 71.975)

    assert result["available"] is False
    assert result["error"]
    assert result.get("current_m3s") is None


@respx.mock
def test_nulls_in_the_series_do_not_fabricate_a_reading() -> None:
    """Open-Meteo returns null where the model has no value. A null today must
    not silently become a number."""
    respx.get(FLOOD_URL).mock(
        return_value=httpx.Response(
            200, json=payload([None] * 7 + [None] + [None] * 6)
        )
    )

    result = river.discharge_for(34.015, 71.975)

    assert result["available"] is False
    assert result.get("current_m3s") is None


@respx.mock
def test_trend_is_unknown_when_the_forecast_is_missing() -> None:
    """Today is known but the forward series is not: say so, do not guess."""
    respx.get(FLOOD_URL).mock(
        return_value=httpx.Response(200, json=payload([800.0] * 7 + [834.6] + [None] * 6))
    )

    result = river.discharge_for(34.015, 71.975)

    assert result["available"] is True
    assert result["current_m3s"] == 834.6
    assert result["trend"] == "unknown"


@respx.mock
def test_http_error_is_reported_with_its_status() -> None:
    respx.get(FLOOD_URL).mock(return_value=httpx.Response(503))

    result = river.discharge_for(34.015, 71.975)

    assert result["available"] is False
    assert "503" in str(result["error"])


@respx.mock
def test_names_the_model_it_is_quoting() -> None:
    """A discharge figure with no provenance is not auditable."""
    respx.get(FLOOD_URL).mock(
        return_value=httpx.Response(200, json=payload(series(834.6, [830.0])))
    )

    result = river.discharge_for(34.015, 71.975)

    assert "GloFAS" in str(result["model"])
    assert result["source_url"].startswith("https://flood-api.open-meteo.com")


@pytest.mark.parametrize("lat,lon", [(91.0, 0.0), (0.0, 181.0)])
def test_refuses_coordinates_outside_the_world(lat: float, lon: float) -> None:
    """Guard at the boundary rather than sending nonsense to the service."""
    result = river.discharge_for(lat, lon)

    assert result["available"] is False
    assert "coordinate" in str(result["error"]).lower()
