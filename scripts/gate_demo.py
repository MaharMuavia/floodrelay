"""The human gate, all the way through: refuse, approve, dispatch, refuse the replay.

Every other demonstration in this repo shows the gate *holding* -- refusing a
dispatch that has no approval. That is the safety property, and it is the one
that matters most. But it is only half the story, and half a story invites the
question the whole design answers: "so the agent can never actually send help?"

It can. This walks the complete lifecycle on the real pipeline, with the real
gate hook on every step:

  1. A rescue call comes in. The agent extracts, geolocates, scores and matches
     it -- and stops at the gate with a life-safety `DecisionCard`. Nothing is
     dispatched. `roster_assign` at this point would raise.
  2. The coordinator approves *that exact* request and resource.
  3. `resume_after_decision` re-enters the graph, and `roster_assign` now runs
     -- because a resolved, unspent card authorises it. A boat leaves the jetty.
  4. The same approval is replayed. It is refused: one approval, one dispatch.

Run it under any provider. With a tool-calling one the model chooses the tools;
either way the gate behaves identically, because the gate is code, not a prompt.

Usage:
    cd backend && uv run python ../scripts/gate_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND / "src"))

RESCUE = "5 log chhat par phanse hain, 2 bacche hain, Pir Sabak, pani gale tak aa gaya"


def _rule(title: str) -> None:
    print()
    print(f"--- {title} " + "-" * max(0, 66 - len(title)))


def main() -> int:
    from floodrelay.agent.hooks.human_gate import GateViolation, enforce_gate
    from floodrelay.agent.models import describe_models
    from floodrelay.agent.tools.roster import assign
    from floodrelay.services import seed
    from floodrelay.services.pipeline import PipelineService, set_pipeline_service
    from floodrelay.store.decisions_repo import DecisionsRepo
    from floodrelay.store.requests_repo import RequestsRepo
    from floodrelay.store.resources_repo import ResourcesRepo

    models = describe_models()
    print(f"provider: {models['provider']}   tool calling: {models['tool_calling']}")

    seed.reset()
    service = PipelineService(use_model=True)
    set_pipeline_service(service)
    decisions = DecisionsRepo()
    requests = RequestsRepo()
    resources = ResourcesRepo()

    # 1. Intake -> gate holds -----------------------------------------------
    _rule("1. a rescue call arrives, and the agent stops at the gate")
    request = service.accept(RESCUE, channel="whatsapp")
    state = service.process(request)
    card = state.card

    print(f"message : {RESCUE}")
    print(f"kind    : {state.request.need.kind if state.request.need else '?'}")
    print(f"location: {state.request.location.label if state.request.location else 'none'}")
    print(f"matched : {state.request.matched_resource_id}")
    print(f"status  : {state.request.status}")
    if card is None:
        print("UNEXPECTED: no decision card was raised for a rescue call.")
        return 1
    print(f"card    : {card.kind} -- {card.heading}")
    dispatch_option = next((o for o in card.options if o.is_dispatch), None)
    if dispatch_option is None or not dispatch_option.resource_id:
        print("UNEXPECTED: the life-safety card offered no dispatch option.")
        return 1
    print(f"awaiting a human on request {dispatch_option.request_id} / "
          f"resource {dispatch_option.resource_id}")

    # Prove the gate would refuse a dispatch right now, before approval.
    _rule("   before approval: roster_assign is refused")
    try:
        assign(
            dispatch_option.request_id,
            dispatch_option.resource_id,
            decision_card_id=None,
        )
        print("FAILURE: a dispatch went through with no approval.")
        return 1
    except GateViolation as exc:
        print(f"refused, correctly: {exc}")

    resource_before = resources.require(dispatch_option.resource_id)
    print(f"resource {resource_before.id} status: {resource_before.status} (untouched)")

    # 2 + 3. The coordinator approves, and the dispatch runs ----------------
    _rule("2. the coordinator approves this exact request and resource")
    print(f"resolving {card.id} -> {dispatch_option.id} ({dispatch_option.label!r})")
    result = service.resolve_decision(card.id, dispatch_option.id, note="water at the neck")
    print(f"resume outcome: {result['outcomes']}")

    _rule("3. roster_assign now runs, under the approval")
    dispatched = requests.require(dispatch_option.request_id)
    resource_after = resources.require(dispatch_option.resource_id)
    spent = decisions.get(card.id)
    print(f"request {dispatched.id} status : {dispatched.status}")
    print(f"resource {resource_after.id} status: {resource_after.status}")
    print(f"card {card.id} consumed_at   : {spent.consumed_at if spent else None}")
    print(f"card {card.id} consumed_by   : {spent.consumed_by if spent else None}")

    if dispatched.status != "dispatched" or resource_after.status != "assigned":
        print("FAILURE: an approved dispatch did not go through.")
        return 1

    # 4. The replay is refused ----------------------------------------------
    _rule("4. the same approval, replayed, is refused")
    try:
        enforce_gate(
            "roster_assign",
            {
                "request_id": dispatch_option.request_id,
                "resource_id": dispatch_option.resource_id,
            },
            {"decision_card_id": card.id},
        )
        print("FAILURE: one approval authorised a second dispatch.")
        return 1
    except GateViolation as exc:
        print(f"refused, correctly: {exc}")

    _rule("summary")
    print("The gate refused with no approval, allowed exactly the one the")
    print("coordinator gave, and refused the replay. One approval, one dispatch.")
    print()
    print("PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
