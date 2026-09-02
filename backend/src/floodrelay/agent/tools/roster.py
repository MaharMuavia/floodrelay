"""The internal resource roster.

`roster_assign` is dispatch-class: it is the point where a decision stops being
a plan and starts being a boat leaving a jetty. It is listed in
DISPATCH_CLASS_TOOLS and the human gate refuses it without a resolved decision
card. The gate check is repeated inside the function body as well as in the
hook, because defence in depth is cheap and this is the one call that must never
happen by accident.
"""

from __future__ import annotations

from typing import Any

from strands import tool

from ...models.resource import Capability, Resource, ResourceKind
from ...services.geo import eta_minutes, haversine_m
from ...store.requests_repo import RequestsRepo
from ...store.resources_repo import ResourcesRepo

# Which resource kinds can serve which need.
KIND_FOR_NEED: dict[str, tuple[ResourceKind, ...]] = {
    "rescue": ("boat",),
    "medical": ("medical_team", "ambulance"),
    "food_water": ("food_truck",),
    "shelter": ("shelter",),
    "other": (),
}


def search(
    *,
    need_kind: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    capability: Capability | None = None,
    available_only: bool = True,
    repo: ResourcesRepo | None = None,
) -> list[dict[str, Any]]:
    """Capable resources, nearest first when a location is given."""
    resources = (repo or ResourcesRepo()).list_all()
    if available_only:
        resources = [r for r in resources if r.status == "available"]
    if need_kind:
        kinds = KIND_FOR_NEED.get(need_kind, ())
        if kinds:
            resources = [r for r in resources if r.kind in kinds]
    if capability:
        resources = [r for r in resources if capability in r.capabilities]

    rows: list[dict[str, Any]] = []
    for r in resources:
        row: dict[str, Any] = {
            "id": r.id,
            "name": r.name,
            "kind": r.kind,
            "capabilities": list(r.capabilities),
            "capacity": r.capacity,
            "status": r.status,
            "lat": r.lat,
            "lon": r.lon,
        }
        if lat is not None and lon is not None:
            d = haversine_m(lat, lon, r.lat, r.lon)
            row["distance_m"] = round(d, 1)
            row["eta_min"] = eta_minutes(d)
        rows.append(row)

    if lat is not None and lon is not None:
        rows.sort(key=lambda x: x["distance_m"])
    return rows


@tool
def roster_search(
    need_kind: str,
    lat: float | None = None,
    lon: float | None = None,
) -> dict[str, Any]:
    """Find relief resources capable of serving a given need, nearest first.

    Args:
        need_kind: One of rescue, medical, food_water, shelter, other.
        lat: Latitude of the request, if known. Enables distance and ETA.
        lon: Longitude of the request, if known.

    Returns:
        A dict with `resources`: a list of available resources, each with id,
        name, kind, capacity, and (when a location was given) distance_m and
        eta_min. Sorted nearest first.
    """
    rows = search(need_kind=need_kind, lat=lat, lon=lon)
    return {"resources": rows, "count": len(rows)}


def assign(
    request_id: str,
    resource_id: str,
    *,
    decision_card_id: str | None = None,
    resources: ResourcesRepo | None = None,
    requests: RequestsRepo | None = None,
) -> dict[str, Any]:
    """Commit a resource to a request. Dispatch-class: gated.

    Raises:
        GateViolation: if no resolved decision card authorises this exact
            request/resource pair.
    """
    from ..hooks.human_gate import enforce_gate

    # Defence in depth: the hook already ran, and we check again here so that a
    # direct call from a service or a test cannot slip past the gate.
    enforce_gate(
        "roster_assign",
        {"request_id": request_id, "resource_id": resource_id},
        {"decision_card_id": decision_card_id},
    )

    res_repo = resources or ResourcesRepo()
    req_repo = requests or RequestsRepo()

    resource: Resource = res_repo.assign(resource_id, request_id)
    request = req_repo.require(request_id)
    request.matched_resource_id = resource_id
    request.status = "dispatched"
    req_repo.save(request)

    return {
        "assigned": True,
        "request_id": request_id,
        "resource_id": resource_id,
        "resource_name": resource.name,
    }


@tool
def roster_assign(request_id: str, resource_id: str) -> dict[str, Any]:
    """Commit a resource to a request and mark it dispatched.

    This sends real help to a real place. It cannot be called without a decision
    card that a human coordinator has resolved in favour of this exact request
    and resource; any other call raises GateViolation.

    Args:
        request_id: The help request to serve.
        resource_id: The resource to commit.

    Returns:
        A dict confirming the assignment.
    """
    # decision_card_id is injected by the pipeline via invocation_state; a bare
    # model-initiated call arrives without one and is refused by the gate.
    return assign(request_id, resource_id, decision_card_id=None)
