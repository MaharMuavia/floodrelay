"""The match node: pick the nearest capable available resource.

Selection is arithmetic (see services/geo.py) rather than a model judgement, for
the same reason routing is: "which of these is closer" must be right every time.

Matching is also where contention surfaces. Two open rescues and one boat is the
scarcity this whole console exists to handle, and when it happens the node marks
it rather than silently picking a winner -- the gate turns that mark into a card
for a human.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...models.request import HelpRequest
from ...models.resource import Resource
from ...services.geo import eta_minutes, haversine_m
from ...store.requests_repo import RequestsRepo
from ...store.resources_repo import ResourcesRepo
from ..tools.roster import KIND_FOR_NEED


@dataclass(frozen=True)
class MatchResult:
    resource: Resource | None
    distance_m: float | None
    eta_min: float | None
    reason: str
    contends_with: list[str]

    @property
    def matched(self) -> bool:
        return self.resource is not None

    @property
    def has_conflict(self) -> bool:
        return bool(self.contends_with)


def run(
    request: HelpRequest,
    *,
    resources: ResourcesRepo | None = None,
    requests: RequestsRepo | None = None,
) -> MatchResult:
    """Find the best resource for one request and note who else wants it."""
    if request.need is None:
        return MatchResult(None, None, None, "nothing extracted yet", [])
    if request.location is None:
        return MatchResult(None, None, None, "no confirmed location to match against", [])

    kinds = KIND_FOR_NEED.get(request.need.kind, ())
    if not kinds:
        return MatchResult(
            None, None, None,
            f"a {request.need.kind} request does not map to a dispatchable resource", [],
        )

    res_repo = resources or ResourcesRepo()
    pool = [r for r in res_repo.list_all() if r.kind in kinds and r.status == "available"]
    if not pool:
        return MatchResult(
            None, None, None,
            f"no {' or '.join(kinds)} is available", [],
        )

    lat, lon = request.location.lat, request.location.lon
    ranked = sorted(pool, key=lambda r: haversine_m(lat, lon, r.lat, r.lon))
    best = ranked[0]
    distance = haversine_m(lat, lon, best.lat, best.lon)
    eta = eta_minutes(distance)

    # Who else is already pointed at this resource?
    req_repo = requests or RequestsRepo()
    contends = [
        other.id
        for other in req_repo.list_open()
        if other.id != request.id and other.matched_resource_id == best.id
    ]

    reason = (
        f"{best.name} is the nearest available {best.kind}, "
        f"{distance / 1000:.1f} km away, about {eta:.0f} minutes at flood speed"
    )
    if len(ranked) == 1:
        reason += f". It is the only {best.kind} available"

    return MatchResult(best, round(distance, 1), eta, reason, contends)
