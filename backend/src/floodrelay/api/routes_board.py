"""The board: what the queue, the map and the request detail screen read."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..models.request import HelpRequest, RequestStatus
from ..services.scoring import WEIGHTS, compute_urgency
from .deps import AuditDep, RequestsDep, ResourcesDep

router = APIRouter(tags=["board"])


def _row(request: HelpRequest) -> dict[str, Any]:
    """The shape a queue row needs, and nothing more."""
    need = request.need
    return {
        "id": request.id,
        "status": request.status,
        "urgency": request.urgency,
        "kind": need.kind if need else None,
        "received_at": request.received_at.isoformat(),
        "channel": request.channel,
        "summary": request.raw_text[:160],
        "people_total": need.people_total if need else None,
        "children": need.children if need else None,
        "elderly": need.elderly if need else None,
        "disabled": need.disabled if need else None,
        "pregnant": need.pregnant if need else None,
        "water_level_note": need.water_level_note if need else None,
        "lat": request.location.lat if request.location else None,
        "lon": request.location.lon if request.location else None,
        "location_label": request.location.label if request.location else None,
        "location_confidence": (
            request.location.confidence.score if request.location else None
        ),
        "matched_resource_id": request.matched_resource_id,
        "duplicate_of": request.duplicate_of,
        "photo_key": request.photo_key,
        "photo_severity": request.photo_severity,
        "trace_id": request.trace_id,
    }


@router.get("/board")
def get_board(
    requests: RequestsDep,
    resources: ResourcesDep,
    status: RequestStatus | None = None,
    since: datetime | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    """Board state, most urgent first."""
    rows = requests.list_all()
    if status is not None:
        rows = [r for r in rows if r.status == status]
    if since is not None:
        cutoff = since if since.tzinfo else since.replace(tzinfo=rows[0].received_at.tzinfo)
        rows = [r for r in rows if r.received_at >= cutoff]

    open_rows = [r for r in rows if r.status in {"new", "processing", "needs_decision", "matched"}]
    return {
        "requests": [_row(r) for r in rows[:limit]],
        "counts": {
            "total": len(rows),
            "open": len(open_rows),
            "needs_decision": sum(1 for r in rows if r.status == "needs_decision"),
            "dispatched": sum(1 for r in rows if r.status == "dispatched"),
            "duplicate": sum(1 for r in rows if r.status == "duplicate"),
        },
        "resources": [
            {
                "id": r.id,
                "name": r.name,
                "kind": r.kind,
                "status": r.status,
                "capacity": r.capacity,
                "lat": r.lat,
                "lon": r.lon,
                "current_assignment": r.current_assignment,
            }
            for r in resources.list_all()
        ],
    }


@router.get("/requests/{request_id}")
def get_request(request_id: str, requests: RequestsDep, audit: AuditDep) -> dict[str, Any]:
    request = requests.get(request_id)
    if request is None:
        raise HTTPException(status_code=404, detail=f"No request {request_id}")

    breakdown = compute_urgency(
        request.need,
        raw_text=request.raw_text,
        photo_severity=request.photo_severity,
        received_at=request.received_at,
    )
    return {
        **_row(request),
        "raw_text": request.raw_text,
        "need": request.need.model_dump(mode="json") if request.need else None,
        "location": request.location.model_dump(mode="json") if request.location else None,
        "node_history": request.node_history,
        "geo_attempts": request.geo_attempts,
        "urgency_breakdown": breakdown.as_dict(),
        "urgency_weights": WEIGHTS,
        "audit": [e.model_dump(mode="json") for e in audit.list_for_request(request_id)],
    }


@router.get("/map/heatmap")
def get_heatmap(requests: RequestsDep) -> dict[str, Any]:
    from ..agent.tools.routing import heatmap

    return {"cells": heatmap(requests), "cell_deg": 0.01}


@router.get("/urgency/formula")
def get_formula() -> dict[str, Any]:
    """The scoring formula, so the UI can show it on hover rather than hard-coding it."""
    from ..services.scoring import KIND_WEIGHT, RECENCY_HALFLIFE

    return {
        "weights": WEIGHTS,
        "kind_weights": KIND_WEIGHT,
        "recency_window_hours": RECENCY_HALFLIFE.total_seconds() / 3600,
        "note": "Urgency is computed by a fixed formula, never by the language model.",
    }
