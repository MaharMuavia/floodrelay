"""The help request and everything extracted from it."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .common import Confidence, GeoPoint, Strict, utcnow

NeedKind = Literal["rescue", "medical", "food_water", "shelter", "other"]
Channel = Literal["form", "whatsapp", "sms", "bulk_paste"]
RequestStatus = Literal[
    "new", "processing", "needs_decision", "matched", "dispatched", "closed", "duplicate"
]


class ExtractedNeed(Strict):
    """What the model is allowed to read out of a message.

    Counts are optional on purpose: `None` means the message did not say, and
    that is a different fact from zero. Nodes downstream must not coerce one
    into the other.
    """

    kind: NeedKind
    people_total: int | None = Field(default=None, ge=0, le=10_000)
    children: int | None = Field(default=None, ge=0, le=10_000)
    elderly: int | None = Field(default=None, ge=0, le=10_000)
    disabled: bool | None = None
    pregnant: bool | None = None
    water_level_note: str | None = None
    raw_location_text: str | None = None
    contact_hint: str | None = None  # redacted before it is stored
    extraction_confidence: Confidence


class HelpRequest(Strict):
    id: str
    received_at: datetime = Field(default_factory=utcnow)
    channel: Channel
    raw_text: str  # PII-redacted
    photo_key: str | None = None
    photo_severity: float | None = Field(default=None, ge=0, le=1)
    need: ExtractedNeed | None = None
    location: GeoPoint | None = None
    urgency: float | None = Field(default=None, ge=0, le=1)
    status: RequestStatus = "new"
    duplicate_of: str | None = None
    matched_resource_id: str | None = None
    trace_id: str | None = None

    # Bookkeeping the graph needs and the UI shows; not part of the brief's
    # minimal shape but required to make the retry loop and history legible.
    geo_attempts: int = 0
    node_history: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utcnow)
