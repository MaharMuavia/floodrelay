"""Graph behaviour around the gate and resume.

These cover the things that only showed up when the system was actually run:
the losing side of a conflict must be re-matched rather than treated as an
error, and re-entering the graph must not hand the coordinator the same question
twice. Model calls are off; the gate and resume logic are deterministic.
"""

from __future__ import annotations

import pytest

from conftest import make_need, make_request, make_resource
from floodrelay.agent.graph import Pipeline
from floodrelay.agent.nodes import gate, match
from floodrelay.models.common import Confidence, GeoPoint
from floodrelay.store.decisions_repo import DecisionsRepo
from floodrelay.store.requests_repo import RequestsRepo
from floodrelay.store.resources_repo import ResourcesRepo
from floodrelay.store.table import Table

NOWSHERA = (34.0151, 71.9747)


def _placed(label: str = "Pir Sabak, Nowshera District") -> GeoPoint:
    return GeoPoint(
        lat=NOWSHERA[0], lon=NOWSHERA[1], label=label,
        source="nominatim",
        confidence=Confidence(score=0.82, reason="test fixture"),
    )


def _pipeline(table: Table) -> Pipeline:
    return Pipeline(
        requests=RequestsRepo(table),
        resources=ResourcesRepo(table),
        decisions=DecisionsRepo(table),
        use_model=False,
    )


def _rescue(request_id: str, urgency: float) -> object:
    return make_request(
        request_id,
        need=make_need(kind="rescue", people_total=4),
        location=_placed(),
        urgency=urgency,
        status="matched",
        matched_resource_id="res_boat_1",
    )


# --- the gate rules ---------------------------------------------------------


def test_a_rescue_with_a_good_location_raises_a_life_safety_card() -> None:
    request = _rescue("r_1", 0.9)
    outcome = gate.evaluate(request, match=None)  # type: ignore[arg-type]
    assert outcome.halts
    assert outcome.card is not None
    assert outcome.card.kind == "life_safety"
    assert "rescue" in outcome.reason


def test_a_food_request_with_a_good_location_proceeds_alone() -> None:
    request = make_request(
        "r_2",
        need=make_need(kind="food_water"),
        location=_placed(),
        urgency=0.2,
    )
    outcome = gate.evaluate(request, match=None)  # type: ignore[arg-type]
    assert not outcome.halts
    assert "may proceed alone" in outcome.reason


def test_a_missing_location_always_halts() -> None:
    request = make_request("r_3", need=make_need(kind="food_water"), location=None)
    outcome = gate.evaluate(request, match=None)  # type: ignore[arg-type]
    assert outcome.halts
    assert outcome.card is not None
    assert outcome.card.kind == "low_confidence_location"


def test_the_no_location_card_does_not_offer_a_meaningless_option() -> None:
    """"Use nowhere" is not something a coordinator can act on."""
    request = make_request("r_4", need=make_need(kind="food_water"), location=None)
    card = gate.low_confidence_location_card(request)
    assert "ACCEPT" not in {o.id for o in card.options}
    assert {"PICK", "ASK"} <= {o.id for o in card.options}


def test_every_card_offers_an_option_that_dispatches_nobody() -> None:
    request = _rescue("r_5", 0.9)
    for card in (
        gate.life_safety_card(request, None),  # type: ignore[arg-type]
        gate.low_confidence_location_card(request),
    ):
        assert any(not o.is_dispatch for o in card.options), card.kind


# --- resume after a decision ------------------------------------------------


def test_the_losing_side_of_a_conflict_is_rematched_not_errored(table: Table) -> None:
    """Losing the boat is a normal outcome, not a failure."""
    resources = ResourcesRepo(table)
    resources.save(make_resource("res_boat_1"))
    requests = RequestsRepo(table)
    decisions = DecisionsRepo(table)

    winner = _rescue("r_win", 0.91)
    loser = _rescue("r_lose", 0.62)
    requests.save(winner)  # type: ignore[arg-type]
    requests.save(loser)  # type: ignore[arg-type]

    result = match.run(winner, resources=resources, requests=requests)  # type: ignore[arg-type]
    rival_result = match.run(loser, resources=resources, requests=requests)  # type: ignore[arg-type]
    card = gate.resource_conflict_card(winner, loser, result, rival_result)  # type: ignore[arg-type]
    decisions.save(card)

    winning_option = next(o for o in card.options if o.request_id == "r_win")
    decisions.resolve(card.id, winning_option.id)

    pipeline = _pipeline(table)
    pipeline.resume_after_decision("r_win", decisions.require(card.id))
    pipeline.resume_after_decision("r_lose", decisions.require(card.id))

    assert requests.require("r_win").status == "dispatched"
    assert requests.require("r_win").matched_resource_id == "res_boat_1"

    loser_after = requests.require("r_lose")
    assert loser_after.status != "dispatched", "the losing request must not be sent a boat"
    assert loser_after.matched_resource_id is None, "it must not still claim the taken boat"


def test_resuming_does_not_raise_a_second_identical_card(table: Table) -> None:
    """Re-entering the graph must not ask the coordinator the same thing twice."""
    ResourcesRepo(table).save(make_resource("res_boat_1"))
    requests = RequestsRepo(table)
    decisions = DecisionsRepo(table)

    request = _rescue("r_1", 0.9)
    requests.save(request)  # type: ignore[arg-type]

    pipeline = _pipeline(table)
    existing = gate.life_safety_card(request, None)  # type: ignore[arg-type]
    decisions.save(existing)

    # A hold answer sends nobody and re-runs match and gate.
    hold = next(o for o in existing.options if not o.is_dispatch)
    decisions.resolve(existing.id, hold.id)
    pipeline.resume_after_decision("r_1", decisions.require(existing.id))

    open_life_safety = [
        c for c in decisions.list_open() if c.kind == "life_safety" and "r_1" in c.request_ids
    ]
    assert len(open_life_safety) <= 1, (
        f"the coordinator was handed {len(open_life_safety)} identical cards for one request"
    )


# --- a failed run must not strand the request -------------------------------
#
# Found by looking at a running board: three requests sat marked
# needs_decision with no card behind them, because the Ollama server had died
# mid-run. The queue showed amber "Needs you" on all three and there was
# nothing to answer and no way forward.


def test_a_failed_run_raises_a_card_the_coordinator_can_answer(table: Table) -> None:
    from floodrelay.services.pipeline import PipelineService

    ResourcesRepo(table).save(make_resource("res_boat_1"))
    requests = RequestsRepo(table)
    decisions = DecisionsRepo(table)

    service = PipelineService(use_model=True)
    request = service.accept("chhat par phanse hain, Pir Sabaq", channel="whatsapp")

    # Stand in for the model being unreachable.
    import floodrelay.agent.nodes.extract as extract_mod

    def boom(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("All connection attempts failed")

    original = extract_mod.run
    extract_mod.run = boom  # type: ignore[assignment]
    try:
        with pytest.raises(ConnectionError):
            service.process(request)
    finally:
        extract_mod.run = original  # type: ignore[assignment]

    stored = requests.require(request.id)
    assert stored.status == "needs_decision"

    cards = [c for c in decisions.list_open() if request.id in c.request_ids]
    assert cards, "a failed run left the request amber with nothing to answer"
    card = cards[0]
    assert card.kind == "processing_failed"
    assert {"RETRY", "MANUAL"} == {o.id for o in card.options}
    assert all(not o.is_dispatch for o in card.options), "a failure must not authorise a dispatch"


def test_every_needs_decision_request_has_an_open_card(table: Table) -> None:
    """The invariant the colour scheme depends on: amber means answerable."""
    from floodrelay.services.pipeline import PipelineService

    ResourcesRepo(table).save(make_resource("res_boat_1"))
    requests = RequestsRepo(table)
    decisions = DecisionsRepo(table)

    service = PipelineService(use_model=False)
    for text in ("chhat par hain Pir Sabaq", "water everywhere please help"):
        req = service.accept(text, channel="sms")
        service.process(req)

    waiting = {r.id for r in requests.list_all() if r.status == "needs_decision"}
    covered = {rid for c in decisions.list_open() for rid in c.request_ids}
    assert waiting <= covered, f"stranded with no card: {sorted(waiting - covered)}"


def test_choosing_to_handle_it_manually_closes_the_request(table: Table) -> None:
    requests = RequestsRepo(table)
    decisions = DecisionsRepo(table)

    request = _rescue("r_1", 0.9)
    requests.save(request)  # type: ignore[arg-type]
    card = gate.processing_failed_card(request, "ConnectError: unreachable")  # type: ignore[arg-type]
    decisions.save(card)
    decisions.resolve(card.id, "MANUAL")

    _pipeline(table).resume_after_decision("r_1", decisions.require(card.id))
    assert requests.require("r_1").status == "closed"
