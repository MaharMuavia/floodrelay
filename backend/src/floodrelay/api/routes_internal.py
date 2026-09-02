"""Internal routes, called by the scheduler rather than by a person.

`/internal/rescan` is what the EventBridge rule targets. It re-scores every open
request so that recency decay actually moves the board: without it, a request
that arrived four hours ago keeps the urgency it was given on arrival, and the
queue slowly stops reflecting reality.

This deliberately does **not** live under `/demo`. An earlier version did, which
meant it was refused whenever `DEMO_MODE=false` -- so the schedule would have
been silently dead in exactly the configuration that needs it.

Authentication: there is none, because the whole service has none (see the
README's non-goals). The route is separated so that adding a shared secret or
locking it to the VPC is a change in one place. `INTERNAL_TOKEN` does that when
set, and is unset by default so local development is unaffected.
"""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, status

from .deps import PipelineDep, SettingsDep

router = APIRouter(prefix="/internal", tags=["internal"])


def _check_token(settings: SettingsDep, supplied: str | None) -> None:
    """Constant-time comparison when a token is configured; a no-op when not."""
    expected = settings.internal_token
    if not expected:
        return
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This endpoint requires the internal token.",
        )


@router.post("/rescan")
def rescan(
    settings: SettingsDep,
    service: PipelineDep,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Re-score open requests. Idempotent, and safe to call on a schedule."""
    _check_token(settings, x_internal_token)
    return service.rescan()


@router.get("/ready")
def ready(settings: SettingsDep) -> dict[str, Any]:
    """Cheap liveness probe with no store access, for the scheduler's own checks."""
    return {"ready": True, "demo_mode": settings.demo_mode}
