"""Show a real Strands Agent calling real tools, on one real message.

This exists because "the agent uses its tools" is a claim, and a claim about an
autonomous system should be checkable by running something. It replays one help
request through the same `PipelineService` the live intake routes use -- no demo
branch, no special casing -- and prints every tool call the model chose to make,
with the arguments it chose and what came back.

It refuses to run against a provider that cannot tool-call, rather than printing
an empty trace that looks like a model declining to use its tools.

Usage:
    cd backend && uv run python ../scripts/tool_trace.py
    cd backend && uv run python ../scripts/tool_trace.py --text "your own message"
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND / "src"))

# A message that gives the model something to actually do: a place name to
# resolve, a headcount to ground, and roof language that must come out `rescue`.
DEFAULT_TEXT = "4 log chhat par phanse hain Pir Sabaq, pani tez barh raha hai, boat bhejo"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", default=DEFAULT_TEXT, help="the message to process")
    args = parser.parse_args()

    from floodrelay.agent.models import describe_models, tool_calling_active
    from floodrelay.config import get_settings
    from floodrelay.services import seed
    from floodrelay.services.events import get_bus
    from floodrelay.services.pipeline import PipelineService, set_pipeline_service
    from floodrelay.store.decisions_repo import DecisionsRepo

    settings = get_settings()
    models = describe_models(settings)

    print("provider     :", models["provider"])
    print("light model  :", models["light"])
    print("heavy model  :", models["heavy"])
    print("tool calling :", models["tool_calling"])
    print()

    if not tool_calling_active(settings):
        print("REFUSING TO RUN.")
        print(models["tool_calling_detail"])
        print()
        print("An empty trace here would look like a model choosing not to use its")
        print("tools. It would actually mean the tools were never offered to it.")
        return 2

    seed.reset()
    service = PipelineService(use_model=True)
    set_pipeline_service(service)

    print(f"message: {args.text}")
    print()
    started = time.time()
    request = service.accept(args.text, channel="whatsapp")
    state = service.process(request)
    elapsed = time.time() - started

    # The same event stream the console's activity feed renders, read back off
    # the bus this run published into.
    events = [e for e in get_bus().recent(200) if e.get("request_id") == request.id]
    all_tools = [e for e in events if e.get("type") == "tool_call"]

    # The distinction this whole script exists to make. A tool the pipeline
    # called from Python is not evidence of anything about the model.
    by_model: list[dict[str, Any]] = [e for e in all_tools if e.get("chosen_by") == "model"]
    by_pipeline: list[dict[str, Any]] = [e for e in all_tools if e.get("chosen_by") != "model"]

    print("--- the run, as the console saw it -------------------------------")
    for event in events:
        kind = event.get("type")
        if kind == "node_start":
            print(f"  NODE  {event['node']}")
        elif kind == "tool_call":
            who = "MODEL" if event.get("chosen_by") == "model" else "pipe "
            print(f"    [{who}] {event['tool']:<16} {event['summary']}")
        elif kind == "node_complete":
            print(f"        -> {event.get('result')}")

    print()
    print("=" * 70)
    print(f"run took {elapsed:.1f}s")
    print(f"nodes visited        : {' -> '.join(state.visited)}")
    print(f"tools CHOSEN BY MODEL: {len(by_model)}")
    for call in by_model:
        print(f"  - {call['tool']}: {call['summary']}")
    print(f"tools called by the pipeline: {len(by_pipeline)}")
    for call in by_pipeline:
        print(f"  - {call['tool']}: {call['summary']}")

    need = state.request.need
    print()
    print(f"kind      : {need.kind if need else '?'}")
    print(f"people    : {need.people_total if need else '?'}")
    print(f"location  : {state.request.location.label if state.request.location else 'none'}")
    print(f"confidence: {state.request.location.confidence.score if state.request.location else 0}")
    print(f"urgency   : {state.request.urgency}")
    print(f"status    : {state.request.status}")

    card = state.card
    print()
    if card is not None:
        print(f"HALTED AT THE GATE: {card.kind}")
        print(f"  {card.heading}")
        for option in card.options:
            mark = "DISPATCH" if option.is_dispatch else "no-op   "
            print(f"    [{mark}] {option.id}: {option.label}")
    else:
        print("no decision card raised")

    dispatched = [r for r in DecisionsRepo().list_all() if r.consumed_at is not None]
    print()
    print(f"approvals consumed : {len(dispatched)}")
    print(f"request dispatched : {state.request.status == 'dispatched'}")

    if not by_model:
        print()
        print("FAILED: the model chose no tools at all.")
        print("Every tool above was called from Python, which is the old behaviour.")
        return 1
    if state.request.status == "dispatched":
        print()
        print("FAILED: something was dispatched with no human approval.")
        return 1

    print()
    print("PASSED: the model called its own tools, and the gate still held.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
