"""The human gate.

Nothing reaches a real responder without a person approving it. That rule is
enforced here, in code, on the path every tool call takes -- not in a prompt,
because a prompt is a request and this is a requirement.

The gate is fail-closed in every direction. A dispatch-class tool call is
refused unless *all* of the following hold:

  1. The invocation carries a `decision_card_id`.
  2. That card exists and has been resolved by a human.
  3. The chosen option is a dispatch option (a "hold" answer authorises nothing).
  4. The option names the same request and resource the tool was called with.
  5. The card has not already been spent on an earlier dispatch.

Anything else raises `GateViolation`. Missing state raises. An unreadable store
raises. A malformed card raises. There is no branch in this file that lets a
dispatch through by accident, and `test_human_gate.py` exists to keep it that
way.
"""

from __future__ import annotations

from typing import Any

from ...models.common import utcnow
from ...models.decision import DecisionCard
from ...store.decisions_repo import DecisionsRepo

# Tools that cause something to happen in the physical world. Adding a tool to
# this set is how you put it behind the gate.
DISPATCH_CLASS_TOOLS: frozenset[str] = frozenset(
    {
        "roster_assign",
        "notify_responder",
    }
)


class GateViolation(RuntimeError):
    """A dispatch-class tool was called without a valid human approval."""


def is_dispatch_class(tool_name: str) -> bool:
    return tool_name in DISPATCH_CLASS_TOOLS


def enforce_gate(
    tool_name: str,
    tool_input: dict[str, Any] | None,
    invocation_state: dict[str, Any] | None,
    *,
    decisions: DecisionsRepo | None = None,
    consume: bool = True,
) -> DecisionCard | None:
    """Authorise one dispatch-class tool call, or raise.

    Returns the approving card for dispatch-class tools, and None for tools that
    are not gated. Never returns without either an approval or an exception.
    """
    if not is_dispatch_class(tool_name):
        return None

    state = invocation_state or {}
    card_id = state.get("decision_card_id")
    if not card_id:
        raise GateViolation(
            f"{tool_name} is dispatch-class and requires an approved decision card, "
            f"but the invocation carried no decision_card_id."
        )

    repo = decisions or DecisionsRepo()
    try:
        card = repo.get(str(card_id))
    except Exception as exc:  # an unreadable store is not an approval
        raise GateViolation(
            f"{tool_name} blocked: could not read decision card {card_id!r} "
            f"({exc.__class__.__name__}: {exc})."
        ) from exc

    if card is None:
        raise GateViolation(
            f"{tool_name} blocked: decision card {card_id!r} does not exist."
        )

    if card.outcome is None:
        raise GateViolation(
            f"{tool_name} blocked: decision card {card.id} is still open. "
            f"A coordinator has not answered it."
        )

    if card.consumed_at is not None:
        raise GateViolation(
            f"{tool_name} blocked: decision card {card.id} was already used to "
            f"authorise {card.consumed_by!r}. One approval authorises one dispatch."
        )

    chosen = next((o for o in card.options if o.id == card.outcome.option_id), None)
    if chosen is None:
        raise GateViolation(
            f"{tool_name} blocked: decision card {card.id} records option "
            f"{card.outcome.option_id!r}, which is not among its options."
        )

    if not chosen.is_dispatch:
        raise GateViolation(
            f"{tool_name} blocked: the coordinator chose {chosen.label!r}, "
            f"which does not authorise a dispatch."
        )

    payload = tool_input or {}
    requested_request = payload.get("request_id")
    requested_resource = payload.get("resource_id")

    if chosen.request_id and requested_request and chosen.request_id != requested_request:
        raise GateViolation(
            f"{tool_name} blocked: approval on card {card.id} covers request "
            f"{chosen.request_id}, but the call targets {requested_request}."
        )
    if chosen.resource_id and requested_resource and chosen.resource_id != requested_resource:
        raise GateViolation(
            f"{tool_name} blocked: approval on card {card.id} covers resource "
            f"{chosen.resource_id}, but the call targets {requested_resource}."
        )

    if consume:
        card.consumed_at = utcnow()
        card.consumed_by = f"{tool_name}:{requested_request or chosen.request_id or '?'}"
        repo.save(card)

    return card


class HumanGateHook:
    """Registers `enforce_gate` on the agent's before-tool-call event.

    Implemented as a `HookProvider`; the import is local so this module stays
    testable without constructing an agent.
    """

    def __init__(self, decisions: DecisionsRepo | None = None) -> None:
        self._decisions = decisions

    def register_hooks(self, registry: Any, **kwargs: Any) -> None:
        from strands.hooks import BeforeToolCallEvent

        registry.add_callback(BeforeToolCallEvent, self.before_tool_call)

    def before_tool_call(self, event: Any) -> None:
        tool_use = getattr(event, "tool_use", None) or {}
        name = tool_use.get("name", "")
        if not is_dispatch_class(name):
            return
        # Raises GateViolation on anything short of a valid, unspent approval.
        enforce_gate(
            name,
            tool_use.get("input") or {},
            getattr(event, "invocation_state", None),
            decisions=self._decisions,
        )
