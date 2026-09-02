"""The safety test.

Every one of these asserts that a dispatch does NOT happen. If this file goes
green while the gate is broken, the product's central promise is broken with it,
so the tests are written to be paranoid rather than tidy: each failure mode gets
its own case, and the one passing case is deliberately narrow.
"""

from __future__ import annotations

import pytest

from conftest import make_request
from floodrelay.agent.hooks.human_gate import (
    DISPATCH_CLASS_TOOLS,
    GateViolation,
    enforce_gate,
    is_dispatch_class,
)
from floodrelay.models.decision import DecisionCard, DecisionOption
from floodrelay.store.decisions_repo import DecisionsRepo
from floodrelay.store.requests_repo import RequestsRepo


def _card(
    decisions: DecisionsRepo,
    *,
    card_id: str = "d_1",
    is_dispatch: bool = True,
    request_id: str = "r_03",
    resource_id: str = "res_boat_1",
) -> DecisionCard:
    card = DecisionCard(
        id=card_id,
        kind="resource_conflict",
        request_ids=[request_id],
        heading="One boat, two calls.",
        reasoning="More people and higher water at A.",
        options=[
            DecisionOption(
                id="A",
                label="Send the boat to A",
                request_id=request_id,
                resource_id=resource_id,
                is_dispatch=is_dispatch,
            ),
            DecisionOption(id="HOLD", label="Neither - hold", is_dispatch=False),
        ],
    )
    return decisions.save(card)


# --- the tool set ----------------------------------------------------------


def test_the_dispatch_class_set_is_not_empty() -> None:
    """A gate over nothing is not a gate."""
    assert DISPATCH_CLASS_TOOLS
    assert is_dispatch_class("roster_assign")
    assert is_dispatch_class("notify_responder")


def test_non_dispatch_tools_pass_through_ungated() -> None:
    assert enforce_gate("geocode", {"query": "Nowshera"}, {}) is None
    assert enforce_gate("rainfall", {"lat": 34.0, "lon": 71.9}, None) is None


# --- the refusals ----------------------------------------------------------


def test_dispatch_without_any_decision_card_raises(decisions_repo: DecisionsRepo) -> None:
    """The headline case: the agent tries to dispatch entirely on its own."""
    with pytest.raises(GateViolation, match="no decision_card_id"):
        enforce_gate(
            "roster_assign",
            {"request_id": "r_03", "resource_id": "res_boat_1"},
            {},
            decisions=decisions_repo,
        )


def test_dispatch_with_no_invocation_state_at_all_raises(
    decisions_repo: DecisionsRepo,
) -> None:
    with pytest.raises(GateViolation):
        enforce_gate(
            "roster_assign",
            {"request_id": "r_03", "resource_id": "res_boat_1"},
            None,
            decisions=decisions_repo,
        )


def test_dispatch_citing_a_nonexistent_card_raises(decisions_repo: DecisionsRepo) -> None:
    with pytest.raises(GateViolation, match="does not exist"):
        enforce_gate(
            "roster_assign",
            {"request_id": "r_03", "resource_id": "res_boat_1"},
            {"decision_card_id": "d_nope"},
            decisions=decisions_repo,
        )


def test_dispatch_on_an_unresolved_card_raises(decisions_repo: DecisionsRepo) -> None:
    """A card that exists but nobody has answered authorises nothing."""
    _card(decisions_repo)
    with pytest.raises(GateViolation, match="still open"):
        enforce_gate(
            "roster_assign",
            {"request_id": "r_03", "resource_id": "res_boat_1"},
            {"decision_card_id": "d_1"},
            decisions=decisions_repo,
        )


def test_a_hold_answer_does_not_authorise_dispatch(decisions_repo: DecisionsRepo) -> None:
    """'Neither - hold' is a real answer, and it means do not send anyone."""
    _card(decisions_repo)
    decisions_repo.resolve("d_1", "HOLD")
    with pytest.raises(GateViolation, match="does not authorise a dispatch"):
        enforce_gate(
            "roster_assign",
            {"request_id": "r_03", "resource_id": "res_boat_1"},
            {"decision_card_id": "d_1"},
            decisions=decisions_repo,
        )


def test_approval_for_one_request_does_not_cover_another(
    decisions_repo: DecisionsRepo,
) -> None:
    """The coordinator approved the boat for r_03, not for r_28."""
    _card(decisions_repo, request_id="r_03")
    decisions_repo.resolve("d_1", "A")
    with pytest.raises(GateViolation, match="covers request r_03"):
        enforce_gate(
            "roster_assign",
            {"request_id": "r_28", "resource_id": "res_boat_1"},
            {"decision_card_id": "d_1"},
            decisions=decisions_repo,
        )


def test_approval_for_one_resource_does_not_cover_another(
    decisions_repo: DecisionsRepo,
) -> None:
    _card(decisions_repo, resource_id="res_boat_1")
    decisions_repo.resolve("d_1", "A")
    with pytest.raises(GateViolation, match="covers resource res_boat_1"):
        enforce_gate(
            "roster_assign",
            {"request_id": "r_03", "resource_id": "res_med_1"},
            {"decision_card_id": "d_1"},
            decisions=decisions_repo,
        )


def test_an_approval_cannot_be_replayed(decisions_repo: DecisionsRepo) -> None:
    """One approval authorises exactly one dispatch."""
    _card(decisions_repo)
    decisions_repo.resolve("d_1", "A")
    payload = {"request_id": "r_03", "resource_id": "res_boat_1"}
    state = {"decision_card_id": "d_1"}

    enforce_gate("roster_assign", payload, state, decisions=decisions_repo)
    with pytest.raises(GateViolation, match="already used"):
        enforce_gate("roster_assign", payload, state, decisions=decisions_repo)


def test_an_unreadable_store_is_not_an_approval(decisions_repo: DecisionsRepo) -> None:
    """Fail closed: infrastructure trouble must never read as consent."""

    class BrokenRepo(DecisionsRepo):
        def get(self, decision_id: str):  # type: ignore[override]
            raise ConnectionError("dynamodb unreachable")

    with pytest.raises(GateViolation, match="could not read decision card"):
        enforce_gate(
            "roster_assign",
            {"request_id": "r_03", "resource_id": "res_boat_1"},
            {"decision_card_id": "d_1"},
            decisions=BrokenRepo(decisions_repo.table),
        )


def test_notify_responder_is_gated_too(decisions_repo: DecisionsRepo) -> None:
    with pytest.raises(GateViolation):
        enforce_gate(
            "notify_responder",
            {"request_id": "r_03", "message": "boat en route"},
            {},
            decisions=decisions_repo,
        )


# --- the one thing that is allowed -----------------------------------------


def test_a_resolved_dispatch_approval_lets_exactly_that_call_through(
    decisions_repo: DecisionsRepo,
) -> None:
    _card(decisions_repo)
    decisions_repo.resolve("d_1", "A", note="water is higher at A")

    card = enforce_gate(
        "roster_assign",
        {"request_id": "r_03", "resource_id": "res_boat_1"},
        {"decision_card_id": "d_1"},
        decisions=decisions_repo,
    )
    assert card is not None
    assert card.id == "d_1"
    assert card.consumed_at is not None


def test_the_gate_does_not_dispatch_anything_by_itself(
    decisions_repo: DecisionsRepo, requests_repo: RequestsRepo
) -> None:
    """Authorising a call must not, on its own, change any request state."""
    requests_repo.save(make_request("r_03", status="matched", matched_resource_id="res_boat_1"))
    _card(decisions_repo)
    decisions_repo.resolve("d_1", "A")
    enforce_gate(
        "roster_assign",
        {"request_id": "r_03", "resource_id": "res_boat_1"},
        {"decision_card_id": "d_1"},
        decisions=decisions_repo,
    )
    assert requests_repo.require("r_03").status == "matched"
