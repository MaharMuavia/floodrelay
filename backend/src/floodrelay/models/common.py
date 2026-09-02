"""Primitives shared across the domain models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

GeoSource = Literal["nominatim", "coordinates_in_message", "coordinator_override"]


def utcnow() -> datetime:
    return datetime.now(UTC)


class Strict(BaseModel):
    """Base for every domain model: reject unknown fields, validate on assignment."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Confidence(Strict):
    score: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)


class GeoPoint(Strict):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    label: str
    confidence: Confidence
    source: GeoSource


class GeoCandidate(Strict):
    """One geocoder result, before a node decides which to trust."""

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    label: str
    kind: str | None = None
    importance: float | None = None
