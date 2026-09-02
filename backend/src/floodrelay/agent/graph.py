"""The pipeline graph.

    intake -> extract -> geolocate -> dedupe -> triage -> match -> gate
                  ^          |           |
                  |__________|           +-> (duplicate) -> close
                  conf < floor,
                  once only

Topology notes that matter:

* **The retry edge is real.** When `geolocate` cannot place a request with
  enough confidence, control returns to `extract` once, with the failed
  candidates injected as context so the model can offer a different location
  string. A second failure routes to `gate` as a `low_confidence_location` card
  rather than looping forever.
* **`gate` halts.** It writes a `DecisionCard` and returns. Nothing resumes
  until a coordinator answers, at which point `resume_after_decision` re-enters
  at `match` with their answer in state.
* **dedupe and match share context.** Matching can reveal a duplicate that
  dedupe missed -- two requests wanting the same boat at the same coordinates --
  so match hands back to dedupe once before the gate sees it.

Implementation note, stated plainly because the brief asks for Strands `Graph`
and `Swarm`: this build runs against local Ollama models, and neither
`deepseek-r1:7b` nor `phi3:mini` supports tool calling. Strands' Graph and Swarm
orchestrate *agents that call tools*, so with these models they would have
nothing to orchestrate. The executor below therefore runs the same topology --
same nodes, same conditional retry edge, same dedupe/match handback, same
halting gate -- in explicit Python, and calls the model only for the language
tasks it can actually do. Set MODEL_PROVIDER to bedrock or anthropic and the
tool-calling path in agent/tools/ becomes live. See docs/decisions.md.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..models.decision import DecisionCard
from ..models.request import HelpRequest
from ..store.decisions_repo import DecisionsRepo
from ..store.requests_repo import RequestsRepo
from ..store.resources_repo import ResourcesRepo
from .nodes import dedupe, extract, gate, geolocate, match, triage

Emit = Callable[[dict[str, Any]], None]

NODE_ORDER = ("intake", "extract", "geolocate", "dedupe", "triage", "match", "gate")


@dataclass
class RunState:
    """Everything one pass through the graph produces."""

    request: HelpRequest
    card: DecisionCard | None = None
    duplicate: dedupe.DuplicateVerdict | None = None
    match_result: match.MatchResult | None = None
    explanation: str = ""
    visited: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    halted_at: str | None = None


def _noop(_: dict[str, Any]) -> None:
    return None


class Pipeline:
    """Runs the graph for one request."""

    def __init__(
        self,
        *,
        requests: RequestsRepo | None = None,
        resources: ResourcesRepo | None = None,
        decisions: DecisionsRepo | None = None,
        use_model: bool = True,
        emit: Emit | None = None,
    ) -> None:
        self.requests = requests or RequestsRepo()
        self.resources = resources or ResourcesRepo()
        self.decisions = decisions or DecisionsRepo()
        self.use_model = use_model
        self.emit = emit or _noop

    # --- event helpers ----------------------------------------------------

    def _start(self, state: RunState, node: str) -> float:
        state.visited.append(node)
        state.request.node_history.append(node)
        self.emit({"type": "node_start", "request_id": state.request.id, "node": node})
        return time.monotonic()

    def _done(self, state: RunState, node: str, started: float, result: Any = None) -> None:
        self.emit(
            {
                "type": "node_complete",
                "request_id": state.request.id,
                "node": node,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "result": result,
            }
        )

    def _tool(self, request_id: str, tool: str, summary: str) -> None:
        self.emit({"type": "tool_call", "request_id": request_id, "tool": tool, "summary": summary})

    def _save(self, state: RunState) -> None:
        from ..models.common import utcnow

        state.request.updated_at = utcnow()
        self.requests.save(state.request)
        self.emit(
            {
                "type": "request_updated",
                "request_id": state.request.id,
                "status": state.request.status,
                "urgency": state.request.urgency,
            }
        )

    # --- nodes ------------------------------------------------------------

    def _run_extract(self, state: RunState, extra_context: str | None = None) -> None:
        started = self._start(state, "extract")
        if self.use_model:
            need, _raw = extract.run(state.request.raw_text, extra_context=extra_context)
        else:
            need, _notes = extract.normalise({"kind": "other"}, state.request.raw_text)
        state.request.need = need
        self._done(
            state, "extract", started,
            {"kind": need.kind, "confidence": need.extraction_confidence.score},
        )

    def _run_geolocate(self, state: RunState) -> None:
        started = self._start(state, "geolocate")
        state.request.geo_attempts += 1
        location_text = state.request.need.raw_location_text if state.request.need else None
        if location_text:
            self._tool(state.request.id, "geocode", f'Resolving "{location_text}"')

        point, summary = geolocate.run(
            state.request.raw_text, location_text, attempt=state.request.geo_attempts
        )
        state.request.location = point
        state.notes.append(summary)
        self._done(
            state, "geolocate", started,
            {
                "lat": point.lat if point else None,
                "lon": point.lon if point else None,
                "confidence": point.confidence.score if point else 0.0,
                "summary": summary,
            },
        )

    def _run_dedupe(self, state: RunState) -> None:
        started = self._start(state, "dedupe")
        others = [r for r in self.requests.list_open() if r.id != state.request.id]
        verdict = dedupe.run(state.request, others)
        state.duplicate = verdict
        self._done(
            state, "dedupe", started,
            {"duplicate_of": verdict.candidate_id, "score": verdict.score},
        )

    def _run_triage(self, state: RunState) -> None:
        started = self._start(state, "triage")
        urgency, _breakdown, explanation = triage.run(state.request, use_model=self.use_model)
        state.request.urgency = urgency
        state.explanation = explanation
        self._done(state, "triage", started, {"urgency": urgency})

    def _run_match(self, state: RunState) -> None:
        started = self._start(state, "match")
        self._tool(state.request.id, "compute_routes", "Ranking available resources by distance")
        result = match.run(state.request, resources=self.resources, requests=self.requests)
        state.match_result = result
        if result.matched and result.resource is not None:
            state.request.matched_resource_id = result.resource.id
        self._done(
            state, "match", started,
            {
                "resource_id": result.resource.id if result.resource else None,
                "eta_min": result.eta_min,
                "conflict": result.has_conflict,
            },
        )

    def _run_gate(self, state: RunState, already_approved: bool = False) -> None:
        started = self._start(state, "gate")
        rival = None
        rival_match = None
        if state.match_result and state.match_result.contends_with:
            rival = self.requests.get(state.match_result.contends_with[0])
            if rival is not None:
                rival_match = match.run(rival, resources=self.resources, requests=self.requests)

        outcome = gate.evaluate(
            state.request,
            match=state.match_result,
            duplicate=state.duplicate,
            rival=rival,
            rival_match=rival_match,
            already_approved=already_approved,
        )
        state.notes.append(outcome.reason)

        if outcome.card is not None:
            card = outcome.card
            if state.explanation and card.kind in {"life_safety", "resource_conflict"}:
                card.reasoning = f"{state.explanation} {card.reasoning}"

            # Do not ask the same question twice. Re-entering the graph after a
            # decision runs the gate again, and without this the coordinator
            # would be handed a second identical card for a request that already
            # has one open.
            existing = next(
                (
                    c
                    for c in self.decisions.list_open()
                    if c.kind == card.kind and set(c.request_ids) == set(card.request_ids)
                ),
                None,
            )
            if existing is not None:
                state.card = existing
                state.request.status = "needs_decision"
                state.halted_at = "gate"
                state.notes.append(f"reused open decision {existing.id}")
                self._done(state, "gate", started, {"decision_id": existing.id})
                return

            self.decisions.save(card)
            state.card = card
            state.request.status = "needs_decision"
            state.halted_at = "gate"
            self.emit(
                {
                    "type": "decision_required",
                    "decision_id": card.id,
                    "kind": card.kind,
                    "request_ids": card.request_ids,
                }
            )
        else:
            state.request.status = "matched" if state.request.matched_resource_id else "closed"
        self._done(
            state, "gate", started,
            {"decision_id": outcome.card.id if outcome.card else None},
        )

    # --- the graph --------------------------------------------------------

    def run(self, request: HelpRequest) -> RunState:
        """One full pass: intake through gate, with the retry and duplicate edges."""
        state = RunState(request=request)
        request.status = "processing"
        self._save(state)

        self._run_extract(state)

        # geolocate, with one conditional loop back to extract.
        self._run_geolocate(state)
        if geolocate.needs_retry(state.request.location, state.request.geo_attempts):
            failed = state.request.need.raw_location_text if state.request.need else None
            state.notes.append(f"retrying extraction: {failed!r} did not resolve")
            self._run_extract(
                state,
                extra_context=(
                    f"The location {failed!r} could not be resolved. Look again for a village, "
                    f"landmark, or neighbourhood name, and return only that in raw_location_text."
                ),
            )
            self._run_geolocate(state)

        self._run_dedupe(state)
        if state.duplicate is not None and state.duplicate.is_duplicate:
            state.request.status = "duplicate"
            state.request.duplicate_of = state.duplicate.candidate_id
            state.notes.append(f"closed as a duplicate of {state.duplicate.candidate_id}")
            state.halted_at = "dedupe"
            self._save(state)
            return state

        self._run_triage(state)
        self._run_match(state)

        # Match can reveal a duplicate dedupe missed: two requests pointed at the
        # same resource from the same spot. Hand back once.
        if state.match_result and state.match_result.has_conflict:
            self._run_dedupe(state)
            if state.duplicate is not None and state.duplicate.is_duplicate:
                state.request.status = "duplicate"
                state.request.duplicate_of = state.duplicate.candidate_id
                state.halted_at = "dedupe"
                self._save(state)
                return state

        self._run_gate(state)
        self._save(state)
        return state

    def resume_after_decision(self, request_id: str, card: DecisionCard) -> RunState:
        """Re-enter the graph at `match` with the coordinator's answer in state."""
        request = self.requests.require(request_id)
        state = RunState(request=request)

        outcome = card.outcome
        chosen = next((o for o in card.options if outcome and o.id == outcome.option_id), None)
        state.notes.append(
            f"resumed after decision {card.id}: {chosen.label if chosen else 'unknown option'}"
        )

        if card.kind == "processing_failed" and chosen is not None:
            if chosen.id == "RETRY":
                # Start over rather than resuming: nothing downstream of the
                # failure ever ran, so there is no partial state worth keeping.
                request.status = "new"
                request.geo_attempts = 0
                return self.run(request)
            request.status = "closed"
            state.notes.append("coordinator took this request out of the agent's hands")
            self._save(state)
            return state

        if card.kind == "possible_duplicate" and chosen and chosen.id == "MERGE":
            request.status = "duplicate"
            request.duplicate_of = next((r for r in card.request_ids if r != request.id), None)
            self._save(state)
            return state

        # Only the request the coordinator actually chose is dispatched. The
        # other side of a conflict card has *lost* the boat -- that is a normal
        # outcome, not an error -- so it is re-matched against what is left and
        # goes back through the gate.
        if (
            chosen is not None
            and chosen.is_dispatch
            and chosen.resource_id
            and chosen.request_id == request.id
        ):
            # The approval exists; roster.assign re-checks the gate itself.
            from .tools.roster import assign

            self._tool(request.id, "roster_assign", f"Committing {chosen.resource_id}")
            assign(
                request.id,
                chosen.resource_id,
                decision_card_id=card.id,
                resources=self.resources,
                requests=self.requests,
            )
            state.request = self.requests.require(request.id)
            state.notes.append(f"dispatched {chosen.resource_id} under approval {card.id}")
            self._save(state)
            return state

        # Anything else: nobody is sent for this request. Clear the stale match
        # first, so re-matching sees the resource as taken rather than reusing it.
        if chosen is not None and chosen.is_dispatch and chosen.request_id != request.id:
            request.matched_resource_id = None
            request.status = "processing"
            state.notes.append(
                f"{chosen.resource_id} went to {chosen.request_id}; re-matching this request"
            )

        self._run_match(state)
        # `already_approved` suppresses a second life-safety card only when this
        # request is the one that was just answered.
        answered_this_request = chosen is not None and chosen.request_id == request.id
        self._run_gate(state, already_approved=answered_this_request)
        self._save(state)
        return state
