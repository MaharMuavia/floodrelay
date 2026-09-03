"""The forward pass, as a Strands `Graph`.

    intake -> extract -> geolocate -> dedupe -> triage -> match -> gate
                  ^          |           ^                 |
                  |__________|           |_________________|
                  conf < floor,          conflict, once only
                  once only

Every arrow above is a real `GraphEdge` with a real condition, and the loop back
to `extract` is a real cycle in a real `strands.multiagent.Graph` -- the SDK
re-arms a node whenever an incoming edge's condition is satisfied by the batch
that just completed, so the retry is expressed as topology rather than as an
`if` in a Python driver.

## What is a graph node here, and what is not

The nodes are `PipelineNode`s: objects satisfying Strands' `AgentBase` protocol
whose work is the deterministic step the pipeline has always run. Two of them --
`extract` and `geolocate` -- run a tool-calling `strands.Agent` inside that step
when the provider supports it (see `tool_agent.py`); `dedupe`, `triage`, `match`
and `gate` are arithmetic and rules, and deliberately have no model in them at
all. Making `gate` an LLM node would mean a rule a model could be talked out of,
which is not a rule.

## Why the graph stops at `gate` instead of waiting

`gate` is terminal: it has no outgoing edges, so when it writes a `DecisionCard`
the run simply ends. Human-in-the-loop is inherently multi-invocation -- the
coordinator may answer in ten seconds or ten minutes, from a different process,
after a restart -- and a graph run that blocked inside a node waiting for them
would hold the pipeline lock, the worker thread and an open store connection for
the duration. `resume_after_decision` therefore stays a separate entry point
that re-runs `match -> gate` with the answer in state. That is a deliberate
boundary, not an omission: the halt is what makes the gate safe to hold.

## What is still explicit, and why

`Pipeline.resume_after_decision` is not a graph. It has one branch per decision
kind, it consumes an approval, and it is the only path that reaches
`roster.assign`. Expressing it as edges would have bought nothing and put the
one code path that can dispatch a boat through a second layer of indirection.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from ..models.request import HelpRequest


class PipelineNode:
    """One step of the forward pass, as a Strands graph node.

    Satisfies the SDK's `AgentBase` protocol -- `__call__`, `invoke_async`,
    `stream_async` -- which is all `GraphBuilder.add_node` requires. The step
    itself is a bound method of `Pipeline`, so the graph orchestrates exactly
    the code the explicit executor used to call, in exactly the same order.

    The node ignores the `ContentBlock` input Strands assembles from upstream
    results. State flows through the shared `RunState` instead, because this
    pipeline's state is a typed `HelpRequest` with a location, an urgency and a
    match -- not a paragraph of text for the next node to re-read.
    """

    def __init__(self, node_id: str, step: Callable[[], None]) -> None:
        self.id = node_id
        self.name = node_id
        self._step = step

    def __call__(self, prompt: Any = None, **kwargs: Any) -> Any:
        from strands._async import run_async

        return run_async(lambda: self.invoke_async(prompt, **kwargs))

    async def invoke_async(self, prompt: Any = None, **kwargs: Any) -> Any:
        result: Any = None
        async for event in self.stream_async(prompt, **kwargs):
            if "result" in event:
                result = event["result"]
        return result

    async def stream_async(
        self, prompt: Any = None, **kwargs: Any
    ) -> AsyncIterator[dict[str, Any]]:
        self._step()
        yield {"result": self._result()}

    def _result(self) -> Any:
        """A minimal `AgentResult`, so the graph can account for the node.

        These nodes do not talk to a model on their own account -- when
        `extract` or `triage` does, the tokens are metered by the Agent inside
        the step and land in the audit trail there. Reporting zero usage here is
        accurate, not a stub.
        """
        from strands.agent.agent_result import AgentResult
        from strands.telemetry.metrics import EventLoopMetrics

        return AgentResult(
            stop_reason="end_turn",
            message={"role": "assistant", "content": [{"text": f"{self.id} complete"}]},
            metrics=EventLoopMetrics(),
            state={},
        )


def build_graph(pipeline: Any, state: Any) -> Any:
    """Wire the forward pass for one request.

    A graph is built per run rather than once per process: the edge conditions
    close over this run's `RunState`, and `GraphBuilder` refuses to reuse a node
    instance anyway. Building it is cheap -- no model is constructed here.
    """
    from strands.multiagent import GraphBuilder

    from .nodes import geolocate

    builder = GraphBuilder()

    builder.add_node(PipelineNode("extract", lambda: _extract(pipeline, state)), "extract")
    builder.add_node(PipelineNode("geolocate", lambda: pipeline._run_geolocate(state)), "geolocate")
    builder.add_node(PipelineNode("dedupe", lambda: pipeline._run_dedupe(state)), "dedupe")
    builder.add_node(PipelineNode("triage", lambda: pipeline._run_triage(state)), "triage")
    builder.add_node(PipelineNode("match", lambda: pipeline._run_match(state)), "match")
    builder.add_node(PipelineNode("gate", lambda: pipeline._run_gate(state)), "gate")

    builder.set_entry_point("extract")
    # Nodes are revisited by design (extract on retry, dedupe on the handback),
    # and each revisit must run the step again rather than replay a cached one.
    builder.reset_on_revisit(True)

    def needs_geo_retry(_graph_state: Any) -> bool:
        return geolocate.needs_retry(state.request.location, state.request.geo_attempts)

    def geo_settled(_graph_state: Any) -> bool:
        return not needs_geo_retry(_graph_state)

    def is_duplicate(_graph_state: Any) -> bool:
        return state.duplicate is not None and state.duplicate.is_duplicate

    def not_duplicate(_graph_state: Any) -> bool:
        return not is_duplicate(_graph_state)

    def first_dedupe_pass(_graph_state: Any) -> bool:
        """dedupe -> triage only on the way down, never on the handback."""
        return not_duplicate(_graph_state) and state.visited.count("dedupe") == 1

    def second_dedupe_pass(_graph_state: Any) -> bool:
        """dedupe -> gate only after match handed back and found no duplicate."""
        return not_duplicate(_graph_state) and state.visited.count("dedupe") > 1

    def has_conflict(_graph_state: Any) -> bool:
        return state.match_result is not None and state.match_result.has_conflict

    def no_conflict(_graph_state: Any) -> bool:
        return not has_conflict(_graph_state)

    builder.add_edge("extract", "geolocate")
    # The retry edge. `needs_retry` is false once `geo_attempts > 1`, so this
    # fires at most once and a second failure falls through to the gate as a
    # low_confidence_location card -- the same rule, now enforced by the edge.
    builder.add_edge("geolocate", "extract", condition=needs_geo_retry)
    builder.add_edge("geolocate", "dedupe", condition=geo_settled)
    # A duplicate is terminal: neither edge out of dedupe is satisfied, no node
    # becomes ready, and the graph run ends there.
    builder.add_edge("dedupe", "triage", condition=first_dedupe_pass)
    builder.add_edge("dedupe", "gate", condition=second_dedupe_pass)
    builder.add_edge("triage", "match")
    # The handback: matching can reveal a duplicate dedupe missed -- two calls
    # wanting the same boat from the same spot.
    builder.add_edge("match", "dedupe", condition=has_conflict)
    builder.add_edge("match", "gate", condition=no_conflict)

    # A cyclic graph has no natural node count, and both cycles are already
    # bounded by their own conditions. This is a backstop against a future edit
    # that breaks one of them, sized well above the longest legitimate run
    # (extract, geolocate, extract, geolocate, dedupe, triage, match, dedupe,
    # gate = nine).
    builder.set_max_node_executions(24)

    return builder.build()


def _extract(pipeline: Any, state: Any) -> None:
    """The extract step, including the context injected on a retry.

    The retry edge carries no payload -- Strands edges are conditions, not
    messages -- so the failed location string is read back off `RunState` here.
    """
    if "extract" not in state.visited:
        pipeline._run_extract(state)
        return

    failed = state.request.need.raw_location_text if state.request.need else None
    state.notes.append(f"retrying extraction: {failed!r} did not resolve")
    pipeline._run_extract(
        state,
        extra_context=(
            f"The location {failed!r} could not be resolved. Look again for a village, "
            f"landmark, or neighbourhood name, and return only that in raw_location_text."
        ),
    )


class GraphRunFailed(RuntimeError):
    """The graph stopped without reaching a terminal node."""


def run_forward(pipeline: Any, state: Any, request: HelpRequest) -> None:
    """Execute the forward pass. Mutates `state`; returns nothing.

    A node that raises propagates unwrapped, which is what
    `PipelineService.process` turns into a `processing_failed` card. Hitting the
    execution ceiling does *not* raise on its own -- the SDK marks the run FAILED
    and returns quietly -- so that case is turned into an exception here. A
    forward pass that silently stopped halfway would leave a request sitting in
    `processing` with nothing to answer, which is the one state this console must
    never show.
    """
    from strands.multiagent.base import Status

    graph = build_graph(pipeline, state)
    result = graph(f"Process help request {request.id}.")

    if result.status is Status.FAILED:
        visited = " -> ".join(state.visited) or "nothing"
        raise GraphRunFailed(
            f"the forward graph stopped without reaching a terminal node "
            f"(ran: {visited})"
        )
