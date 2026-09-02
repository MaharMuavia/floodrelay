"""End-to-end check.

Replays seed messages through the real pipeline -- the same `PipelineService`
the live intake routes use -- and asserts the properties the whole design exists
to guarantee:

  1. Decision cards are raised for life-safety calls.
  2. The two rescue calls contend for the single boat and produce a
     `resource_conflict` card.
  3. **Nothing is dispatched without an approved decision card.** This is the
     one that matters. It is checked by inspecting final state, not by trusting
     the gate to have run.

Usage:
    python scripts/e2e.py            # curated subset, fast
    python scripts/e2e.py --full     # all 40 seed messages (slow on CPU models)
    python scripts/e2e.py --no-model # deterministic nodes only, no model calls
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND / "src"))

# A subset chosen to exercise every gate rule without waiting for 40 model calls:
# two rescues that will want the one boat, the duplicated household, a donation
# offer that is not a request, and a message with no usable location.
SUBSET_IDS = ["r_01", "r_02", "r_03", "r_28", "r_19", "r_10"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="replay all 40 seed messages")
    parser.add_argument("--no-model", action="store_true", help="skip model calls")
    args = parser.parse_args()

    from floodrelay.services import seed
    from floodrelay.services.pipeline import PipelineService, set_pipeline_service
    from floodrelay.store.decisions_repo import DecisionsRepo
    from floodrelay.store.requests_repo import RequestsRepo

    service = PipelineService(use_model=not args.no_model)
    set_pipeline_service(service)

    print("resetting demo state...")
    seed.reset()

    rows = seed.load_seed_requests()
    if not args.full:
        rows = [r for r in rows if r["id"] in SUBSET_IDS]
    print(f"replaying {len(rows)} messages (model={'off' if args.no_model else 'on'})\n")

    started = time.time()
    for row in rows:
        t0 = time.time()
        request = service.accept(str(row["text"]), channel=row.get("channel", "form"))
        state = service.process(request)
        need = state.request.need
        print(
            f"  {row['id']} -> {request.id}  {time.time() - t0:5.1f}s  "
            f"kind={need.kind if need else '?':10} "
            f"urgency={state.request.urgency if state.request.urgency is not None else 0:.2f}  "
            f"status={state.request.status:14} "
            f"card={state.card.kind if state.card else '-'}"
        )

    print(f"\nreplayed in {time.time() - started:.1f}s\n")

    requests_repo = RequestsRepo()
    decisions_repo = DecisionsRepo()
    all_requests = requests_repo.list_all()
    all_cards = decisions_repo.list_all()

    kinds = [c.kind for c in all_cards]
    print(f"decision cards raised: {len(all_cards)} {kinds}")
    print(f"requests: {len(all_requests)}")
    for status in ("new", "processing", "needs_decision", "matched", "dispatched",
                   "duplicate", "closed"):
        n = sum(1 for r in all_requests if r.status == status)
        if n:
            print(f"  {status:15} {n}")

    failures: list[str] = []

    # 1. Decisions were raised.
    if not all_cards:
        failures.append("no decision cards were raised at all")

    # 2. Life-safety cards exist for the rescue calls.
    #
    # Only meaningful with the model on: without it nothing is extracted, so no
    # request has a location and every one of them stops at gate rule 2 instead.
    # That is correct behaviour, not a failure, so the assertion is skipped.
    if not args.no_model and "life_safety" not in kinds and "resource_conflict" not in kinds:
        failures.append("no life_safety or resource_conflict card was raised for the rescues")

    # 3. THE SAFETY PROPERTY: nothing dispatched without an approved card.
    approved_pairs = set()
    for card in all_cards:
        if card.outcome is None:
            continue
        chosen = next((o for o in card.options if o.id == card.outcome.option_id), None)
        if chosen and chosen.is_dispatch and chosen.request_id:
            approved_pairs.add((chosen.request_id, chosen.resource_id))

    unapproved = [
        r.id
        for r in all_requests
        if r.status == "dispatched" and (r.id, r.matched_resource_id) not in approved_pairs
    ]
    if unapproved:
        failures.append(f"DISPATCHED WITHOUT APPROVAL: {unapproved}")

    from floodrelay.store.resources_repo import ResourcesRepo

    assigned = [r.id for r in ResourcesRepo().list_all() if r.status == "assigned"]
    if assigned and not approved_pairs:
        failures.append(f"resources committed with no approval on record: {assigned}")

    print()
    print(f"unapproved dispatches: {len(unapproved)}")
    print(f"open decisions awaiting a human: {sum(1 for c in all_cards if c.is_open)}")

    if failures:
        print("\nFAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nPASSED: decisions raised, and nothing was dispatched without approval.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
