"""Demo control.

`/demo/replay` pushes the seed messages through the same `PipelineService.accept`
that live intake uses. There is no separate demo branch and no scripted outcome.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from ..services import seed
from .deps import ReplayIn, SettingsDep

router = APIRouter(prefix="/demo", tags=["demo"])


def _require_demo_mode(settings: SettingsDep) -> None:
    if not settings.demo_mode:
        raise HTTPException(
            status_code=403,
            detail="Demo endpoints are disabled because DEMO_MODE is false.",
        )


@router.get("/info")
def demo_info(settings: SettingsDep) -> dict[str, Any]:
    return {**seed.seed_summary(), "demo_mode": settings.demo_mode}


@router.post("/reset", status_code=status.HTTP_200_OK)
def demo_reset(settings: SettingsDep) -> dict[str, Any]:
    _require_demo_mode(settings)
    return seed.reset()


@router.post("/replay", status_code=status.HTTP_202_ACCEPTED)
def demo_replay(payload: ReplayIn, settings: SettingsDep) -> dict[str, Any]:
    _require_demo_mode(settings)
    return seed.replay(speed=payload.speed, limit=payload.limit)


@router.post("/warm-geocache")
def demo_warm(settings: SettingsDep) -> dict[str, Any]:
    """Pre-resolve every seed place name so a replay never waits on Nominatim."""
    _require_demo_mode(settings)
    return seed.warm_geocache()


@router.post("/rescan")
def demo_rescan(settings: SettingsDep) -> dict[str, Any]:
    """Convenience alias for the demo UI.

    The scheduler calls POST /internal/rescan instead: this one is behind
    DEMO_MODE, and a scheduled job that silently stops working when demo mode
    is switched off would be worse than no schedule at all.
    """
    _require_demo_mode(settings)
    from ..services.pipeline import get_pipeline_service

    return get_pipeline_service().rescan()
