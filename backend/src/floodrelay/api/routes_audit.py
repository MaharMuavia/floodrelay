"""The audit trail: every autonomous action, with the reasoning behind it."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Query

from .deps import AuditDep

router = APIRouter(tags=["audit"])


@router.get("/audit")
def list_audit(
    audit: AuditDep,
    day: date | None = Query(default=None, alias="date"),
    request_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=2000),
) -> dict[str, Any]:
    if request_id:
        events = audit.list_for_request(request_id)
    elif day is not None:
        events = audit.list_for_day(day, limit=limit)
    else:
        events = audit.list_recent(limit=limit)

    return {
        "events": [e.model_dump(mode="json") for e in events[:limit]],
        "count": len(events),
        "note": "Append-only. Entries are never edited or removed.",
    }
