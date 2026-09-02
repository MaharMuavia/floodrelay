"""Geodesic maths and assignment.

Kept out of the model entirely. An LLM asked to compare two distances will
usually get it right and occasionally get it catastrophically wrong, and "which
of these two households is closer to the only boat" is not a question that
tolerates occasionally.

`compute_routes` in agent/tools/routing.py is the tool wrapper around this; in
the AgentCore deployment it runs inside the Code Interpreter sandbox, and the
functions here are what it executes.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_008.8

# Assumed speed of a small boat or a vehicle picking its way through flooded
# ground. Deliberately pessimistic: a route that looks fast on a map is not fast
# through standing water. Stated in the UI wherever an ETA is shown.
FLOOD_SPEED_KMH = 12.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = radians(lat1), radians(lat2)
    dphi = p2 - p1
    dlam = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def eta_minutes(distance_m: float, speed_kmh: float = FLOOD_SPEED_KMH) -> float:
    """Travel time at the assumed flood speed, rounded to a whole minute."""
    if speed_kmh <= 0:
        raise ValueError("speed_kmh must be positive")
    return round(distance_m / 1000.0 / speed_kmh * 60.0, 1)


@dataclass(frozen=True)
class Leg:
    request_id: str
    resource_id: str
    distance_m: float
    eta_min: float


def greedy_assign(
    requests: list[tuple[str, float, float, float]],
    resources: list[tuple[str, float, float]],
) -> tuple[list[Leg], list[str]]:
    """Assign resources to requests, most urgent request first.

    `requests` are (id, lat, lon, urgency); `resources` are (id, lat, lon).
    Returns the chosen legs plus the ids of requests left unserved.

    Greedy by urgency, then by distance: the most urgent request picks the
    nearest free resource, and so on. This is intentionally simple and
    intentionally explainable -- the coordinator has to be able to follow why a
    boat went where it went, and an optimal-but-opaque assignment is worse than
    a good one they can reason about.
    """
    free = {r[0]: (r[1], r[2]) for r in resources}
    legs: list[Leg] = []
    unserved: list[str] = []

    for req_id, rlat, rlon, _urgency in sorted(requests, key=lambda r: -r[3]):
        if not free:
            unserved.append(req_id)
            continue
        best_id, best_dist = min(
            ((rid, haversine_m(rlat, rlon, plat, plon)) for rid, (plat, plon) in free.items()),
            key=lambda pair: pair[1],
        )
        legs.append(
            Leg(
                request_id=req_id,
                resource_id=best_id,
                distance_m=round(best_dist, 1),
                eta_min=eta_minutes(best_dist),
            )
        )
        del free[best_id]

    return legs, unserved


def heatmap_grid(
    points: list[tuple[float, float, float]], *, cell_deg: float = 0.01
) -> list[dict[str, float]]:
    """Bucket weighted points into a lat/lon grid for the map heatmap.

    `points` are (lat, lon, weight). Roughly 1.1 km cells at the equator, which
    is the right granularity for a district-scale view.
    """
    cells: dict[tuple[int, int], dict[str, float]] = {}
    for lat, lon, weight in points:
        key = (int(lat // cell_deg), int(lon // cell_deg))
        cell = cells.setdefault(
            key,
            {
                "lat": (key[0] + 0.5) * cell_deg,
                "lon": (key[1] + 0.5) * cell_deg,
                "weight": 0.0,
                "count": 0.0,
            },
        )
        cell["weight"] += weight
        cell["count"] += 1
    return sorted(cells.values(), key=lambda c: -c["weight"])
