"""Resource contention detection.

Gate rule 3: two or more open requests matched to the same resource inside the
same window is a decision for a human, not for the agent. One boat cannot go to
two places, and choosing between them is a judgement call about whose life is
more at risk -- which is exactly the class of call this system refuses to make
on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from ..models.request import HelpRequest

# Two matches count as contending if they land within this of each other.
CONTENTION_WINDOW = timedelta(hours=2)

_LIVE_STATUSES = {"new", "processing", "needs_decision", "matched"}


@dataclass(frozen=True)
class Contention:
    resource_id: str
    request_ids: list[str]

    @property
    def is_conflict(self) -> bool:
        return len(self.request_ids) > 1


def find_contentions(
    requests: list[HelpRequest], *, window: timedelta = CONTENTION_WINDOW
) -> list[Contention]:
    """Group still-open requests that are competing for the same resource.

    Requests already dispatched, closed or marked duplicate are excluded: a boat
    that has been sent is not contended for, it is spent.
    """
    by_resource: dict[str, list[HelpRequest]] = {}
    for req in requests:
        if req.status not in _LIVE_STATUSES:
            continue
        if not req.matched_resource_id:
            continue
        by_resource.setdefault(req.matched_resource_id, []).append(req)

    out: list[Contention] = []
    for resource_id, group in sorted(by_resource.items()):
        if len(group) < 2:
            continue
        group.sort(key=lambda r: r.updated_at)
        # Only count them as contending if their matches are close in time;
        # a match from yesterday is stale, not competing.
        cluster: list[HelpRequest] = [group[0]]
        for req in group[1:]:
            if req.updated_at - cluster[0].updated_at <= window:
                cluster.append(req)
            else:
                if len(cluster) > 1:
                    out.append(
                        Contention(resource_id, [r.id for r in _by_urgency(cluster)])
                    )
                cluster = [req]
        if len(cluster) > 1:
            out.append(Contention(resource_id, [r.id for r in _by_urgency(cluster)]))
    return out


def _by_urgency(requests: list[HelpRequest]) -> list[HelpRequest]:
    """Most urgent first, so the decision card lists the agent's pick as A."""
    return sorted(requests, key=lambda r: (-(r.urgency or 0.0), r.id))


def contention_for(
    requests: list[HelpRequest], resource_id: str, *, window: timedelta = CONTENTION_WINDOW
) -> Contention | None:
    for c in find_contentions(requests, window=window):
        if c.resource_id == resource_id:
            return c
    return None
