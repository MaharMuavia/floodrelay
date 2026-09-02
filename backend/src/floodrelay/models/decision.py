"""Decision cards: the only thing that can unblock a gated action."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .common import Strict, utcnow

DecisionKind = Literal[
    "life_safety",
    "low_confidence_location",
    "resource_conflict",
    "possible_duplicate",
    # The agent could not process the message at all. Still a decision:
    # someone has to choose between retrying and handling it by hand.
    "processing_failed",
]


class DecisionOption(Strict):
    id: str
    label: str  # says what will happen, e.g. "Send the boat to A"
    request_id: str | None = None
    resource_id: str | None = None
    is_dispatch: bool = False
    facts: dict[str, str] = Field(default_factory=dict)  # same keys, same order, per option


class DecisionOutcome(Strict):
    option_id: str
    note: str | None = None
    resolved_by: str = "coordinator"


class DecisionCard(Strict):
    id: str
    kind: DecisionKind
    request_ids: list[str]
    heading: str  # a plain sentence, not "Resource Conflict Detected"
    recommendation_option_id: str | None = None
    reasoning: str  # plain language, shown verbatim to the coordinator
    options: list[DecisionOption]
    created_at: datetime = Field(default_factory=utcnow)
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    outcome: DecisionOutcome | None = None
    # Set the moment the approval is spent, so one card cannot authorise
    # two dispatches. See agent/hooks/human_gate.py.
    consumed_at: datetime | None = None
    consumed_by: str | None = None
    trace_id: str | None = None

    @property
    def is_open(self) -> bool:
        return self.outcome is None
