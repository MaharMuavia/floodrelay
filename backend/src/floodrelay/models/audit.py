"""Append-only audit trail."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .common import Strict, utcnow

Actor = Literal["agent", "coordinator", "system"]


class AuditEvent(Strict):
    id: str
    ts: datetime = Field(default_factory=utcnow)
    actor: Actor
    node: str | None = None
    tool: str | None = None
    request_id: str | None = None
    input_digest: str = ""
    output_digest: str = ""
    latency_ms: int | None = None
    tokens: int | None = None
    error: str | None = None
    decision_card_id: str | None = None
    trace_id: str | None = None
