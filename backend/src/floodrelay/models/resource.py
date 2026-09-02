"""Relief resources: the scarce things being allocated."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import Strict

ResourceKind = Literal["boat", "medical_team", "food_truck", "shelter", "ambulance"]
ResourceStatus = Literal["available", "assigned", "offline"]
Capability = Literal[
    "water_rescue", "first_aid", "advanced_medical", "food_distribution",
    "clean_water", "overnight_shelter", "wheelchair_access", "transport",
]


class Resource(Strict):
    id: str
    name: str
    kind: ResourceKind
    capabilities: list[Capability] = Field(default_factory=list)
    capacity: int = Field(ge=0)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    status: ResourceStatus = "available"
    current_assignment: str | None = None  # request id
