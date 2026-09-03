"""Situation context: real data about the real flood, around a synthetic queue.

Everything served here is genuinely live -- NASA satellite imagery, GloFAS river
discharge, NDMA's daily national situation report, ReliefWeb headlines. None of
it is an input to any urgency score, and none of it can reach a dispatch: the
human gate stands between every one of these numbers and any responder.

Availability rule
-----------------
A context source that is down degrades the panel, never the console. Each block
answers for itself, so /context returns 200 with four honest "unavailable"
blocks rather than a 500 that would take the coordinator's screen with it. The
queue, the map and the decision dock are the product; this is furniture.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from ..agent.tools import gdacs, imagery_layers, ndma, reliefweb, river, weather
from ..models.common import utcnow
from .deps import SettingsDep

router = APIRouter(prefix="/context", tags=["context"])

# Centre of the district being coordinated, used when no point is supplied.
DISTRICT_CENTRE = (34.0151, 71.9747)


def _guard(block: str, call: Any) -> dict[str, Any]:
    """Run one source so that its failure cannot take the others with it.

    The tools already return failure as a value rather than raising. This is the
    belt to that braces: an unexpected exception in one integration must not
    blank the whole panel.
    """
    try:
        return dict(call())
    except Exception as exc:
        return {
            "available": False,
            "error": f"{block} failed unexpectedly: {exc.__class__.__name__}: {exc}",
        }


@router.get("")
def context(
    settings: SettingsDep,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
) -> dict[str, Any]:
    """Everything real we know about the flood, for the situation panel."""
    point_lat = lat if lat is not None else DISTRICT_CENTRE[0]
    point_lon = lon if lon is not None else DISTRICT_CENTRE[1]
    district = settings.situation_district
    province = settings.situation_province

    return {
        "district": district,
        "province": province,
        "lat": point_lat,
        "lon": point_lon,
        "river": _guard("river", lambda: river.discharge_for(point_lat, point_lon)),
        "rainfall": _guard("rainfall", lambda: weather.rainfall_for(point_lat, point_lon)),
        "ndma": _guard("ndma", lambda: ndma.situation(district, province)),
        "reliefweb": _guard("reliefweb", lambda: reliefweb.context_for("Pakistan")),
        # The only worldwide source here: how this district's flood sits
        # against every other flood being tracked internationally.
        "gdacs": _guard("gdacs", lambda: gdacs.flood_alerts(country="Pakistan")),
        "imagery": _guard("imagery", imagery_layers.available_layers),
        "fetched_at": utcnow().isoformat(),
        "note": (
            "Situation data only. None of these figures influence any urgency "
            "score, and none of them can authorise a dispatch."
        ),
    }


@router.get("/imagery")
def imagery() -> dict[str, Any]:
    """Satellite layer manifest for the map: which layers, and for which date.

    Separated from /context because the map needs it on load and should not
    wait on a 3 MB PDF being parsed to draw a flood layer.
    """
    return _guard("imagery", imagery_layers.available_layers)
