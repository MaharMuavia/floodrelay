"""Shared request/response shapes and dependencies.

Validation happens at the boundary, once, and loudly. Empty text is rejected,
bulk is capped, photos are capped and type-checked, and coordinates outside
plausible bounds are refused -- so that nothing downstream has to defend itself
against a payload that should never have been accepted.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import Depends
from pydantic import BaseModel, Field, field_validator

from ..config import Settings, get_settings
from ..models.request import Channel
from ..services.pipeline import PipelineService, get_pipeline_service
from ..store.audit_repo import AuditRepo
from ..store.decisions_repo import DecisionsRepo
from ..store.requests_repo import RequestsRepo
from ..store.resources_repo import ResourcesRepo

MAX_BULK_ITEMS = 100
MAX_PHOTO_BYTES = 8 * 1024 * 1024
ALLOWED_PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp"}


class IntakeIn(BaseModel):
    channel: Channel = "form"
    text: str = Field(min_length=1, max_length=4000)
    photo: str | None = None

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("text must contain something other than whitespace")
        return cleaned


class BulkItem(BaseModel):
    channel: Channel = "bulk_paste"
    text: str = Field(min_length=1, max_length=4000)


class BulkIn(BaseModel):
    items: list[BulkItem] = Field(min_length=1, max_length=MAX_BULK_ITEMS)


class IntakeOut(BaseModel):
    request_id: str
    trace_id: str | None = None


class BulkOut(BaseModel):
    accepted: int
    request_ids: list[str]


class ResolveIn(BaseModel):
    option_id: str = Field(min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=1000)
    # Only used by a low_confidence_location card answered with "pick a point".
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)


class ReplayIn(BaseModel):
    speed: float = Field(default=1.0, ge=0.0, le=100.0)
    limit: int | None = Field(default=None, ge=1, le=200)


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    store: str
    models: dict[str, str]
    demo_mode: bool
    checks: dict[str, Any]


def settings_dep() -> Settings:
    return get_settings()


def requests_repo() -> RequestsRepo:
    return RequestsRepo()


def resources_repo() -> ResourcesRepo:
    return ResourcesRepo()


def decisions_repo() -> DecisionsRepo:
    return DecisionsRepo()


def audit_repo() -> AuditRepo:
    return AuditRepo()


def pipeline() -> PipelineService:
    return get_pipeline_service()


SettingsDep = Annotated[Settings, Depends(settings_dep)]
RequestsDep = Annotated[RequestsRepo, Depends(requests_repo)]
ResourcesDep = Annotated[ResourcesRepo, Depends(resources_repo)]
DecisionsDep = Annotated[DecisionsRepo, Depends(decisions_repo)]
AuditDep = Annotated[AuditRepo, Depends(audit_repo)]
PipelineDep = Annotated[PipelineService, Depends(pipeline)]
