"""Nearest-resource routing, ETAs and the severity heatmap.

The arithmetic lives in services/geo.py and is executed here rather than by the
model. Asked to compare two distances, an LLM is usually right and occasionally
very wrong, and "which household is closer to the only boat" does not tolerate
occasionally.

Deployment note: on AgentCore this function body is what runs inside the Code
Interpreter sandbox. Locally it executes in-process. The maths is identical
either way -- the sandbox changes where it runs, not what it computes.
"""

from __future__ import annotations

from typing import Any

from strands import tool

from ...services.geo import FLOOD_SPEED_KMH, greedy_assign, heatmap_grid
from ...store.requests_repo import RequestsRepo
from ...store.resources_repo import ResourcesRepo


def compute(
    request_ids: list[str],
    resource_ids: list[str] | None = None,
    *,
    requests: RequestsRepo | None = None,
    resources: ResourcesRepo | None = None,
) -> dict[str, Any]:
    """Assign resources to located requests and report distance and ETA."""
    req_repo = requests or RequestsRepo()
    res_repo = resources or ResourcesRepo()

    located: list[tuple[str, float, float, float]] = []
    unlocated: list[str] = []
    for rid in request_ids:
        req = req_repo.get(rid)
        if req is None or req.location is None:
            unlocated.append(rid)
            continue
        located.append((rid, req.location.lat, req.location.lon, req.urgency or 0.0))

    if resource_ids is None:
        pool = [r for r in res_repo.list_all() if r.status == "available"]
    else:
        pool = [r for r in (res_repo.get(i) for i in resource_ids) if r is not None]

    legs, unserved = greedy_assign(located, [(r.id, r.lat, r.lon) for r in pool])

    return {
        "assignments": [
            {
                "request_id": leg.request_id,
                "resource_id": leg.resource_id,
                "distance_m": leg.distance_m,
                "distance_km": round(leg.distance_m / 1000, 2),
                "eta_min": leg.eta_min,
            }
            for leg in legs
        ],
        "unserved_request_ids": unserved,
        "unlocated_request_ids": unlocated,
        "assumed_speed_kmh": FLOOD_SPEED_KMH,
    }


@tool
def compute_routes(request_ids: list[str], resource_ids: list[str] | None = None) -> dict[str, Any]:
    """Work out which resource should go to which request, with distances and ETAs.

    Uses haversine distance and a greedy assignment that serves the most urgent
    request first. ETAs assume a slow speed appropriate to flooded ground.
    This is exact arithmetic, not an estimate by a language model.

    Args:
        request_ids: Requests to route. Requests without a resolved location are
            reported separately in `unlocated_request_ids` rather than guessed at.
        resource_ids: Restrict to these resources. Defaults to all available ones.

    Returns:
        A dict with `assignments`, `unserved_request_ids`, `unlocated_request_ids`
        and the `assumed_speed_kmh` used for the ETAs.
    """
    return compute(request_ids, resource_ids)


def heatmap(requests: RequestsRepo | None = None, cell_deg: float = 0.01) -> list[dict[str, float]]:
    """Grid the open requests by urgency for the map layer."""
    repo = requests or RequestsRepo()
    points = [
        (r.location.lat, r.location.lon, r.urgency or 0.0)
        for r in repo.list_open()
        if r.location is not None
    ]
    return heatmap_grid(points, cell_deg=cell_deg)
