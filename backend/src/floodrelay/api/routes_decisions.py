"""Decision cards: the only thing that can unblock a gated action."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..models.common import Confidence, GeoPoint
from ..models.decision import DecisionCard
from .deps import DecisionsDep, PipelineDep, RequestsDep, ResolveIn

router = APIRouter(tags=["decisions"])


def _card(card: DecisionCard) -> dict[str, Any]:
    return {
        "id": card.id,
        "kind": card.kind,
        "request_ids": card.request_ids,
        "heading": card.heading,
        "reasoning": card.reasoning,
        "recommendation_option_id": card.recommendation_option_id,
        "options": [
            {
                "id": o.id,
                "label": o.label,
                "request_id": o.request_id,
                "resource_id": o.resource_id,
                "is_dispatch": o.is_dispatch,
                "facts": o.facts,
            }
            for o in card.options
        ],
        "created_at": card.created_at.isoformat(),
        "resolved_at": card.resolved_at.isoformat() if card.resolved_at else None,
        "resolved_by": card.resolved_by,
        "outcome": card.outcome.model_dump(mode="json") if card.outcome else None,
        "is_open": card.is_open,
        "trace_id": card.trace_id,
    }


@router.get("/decisions")
def list_decisions(decisions: DecisionsDep, open: bool = True) -> dict[str, Any]:
    cards = decisions.list_open() if open else decisions.list_all()
    return {"decisions": [_card(c) for c in cards], "count": len(cards)}


@router.get("/decisions/{decision_id}")
def get_decision(decision_id: str, decisions: DecisionsDep) -> dict[str, Any]:
    card = decisions.get(decision_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"No decision {decision_id}")
    return _card(card)


@router.post("/decisions/{decision_id}/resolve")
def resolve_decision(
    decision_id: str,
    payload: ResolveIn,
    decisions: DecisionsDep,
    requests: RequestsDep,
    service: PipelineDep,
) -> dict[str, Any]:
    """Record the coordinator's answer and resume the graph."""
    card = decisions.get(decision_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"No decision {decision_id}")
    if not card.is_open:
        raise HTTPException(status_code=409, detail=f"Decision {decision_id} is already resolved")
    if payload.option_id not in {o.id for o in card.options}:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Option {payload.option_id!r} is not on this card; "
                f"valid options are {sorted(o.id for o in card.options)}"
            ),
        )

    # "Pick a point on the map" carries the coordinator's own coordinates, which
    # override the geocoder entirely.
    if payload.option_id == "PICK":
        if payload.lat is None or payload.lon is None:
            raise HTTPException(
                status_code=422, detail="Picking a point requires both lat and lon"
            )
        for request_id in card.request_ids:
            request = requests.get(request_id)
            if request is None:
                continue
            request.location = GeoPoint(
                lat=payload.lat,
                lon=payload.lon,
                label="Set by the coordinator",
                source="coordinator_override",
                confidence=Confidence(
                    score=1.0, reason="a coordinator placed this point by hand"
                ),
            )
            requests.save(request)

    try:
        return service.resolve_decision(decision_id, payload.option_id, note=payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
