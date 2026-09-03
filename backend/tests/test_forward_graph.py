"""The topology, pinned.

`test_pipeline.py` covers what the nodes decide. This file covers the shape of
the run itself -- which node follows which, and where it stops -- because that
shape is now expressed as conditions on `GraphEdge`s rather than as `if`
statements a reader can follow line by line.

Each case drives a real `strands.multiagent.Graph` built by `build_graph`, over
a stub pipeline that records the order and sets exactly the state the real nodes
would have set. That isolates the edges from the nodes: a failure here is a
wiring failure, not a scoring one.
"""

from __future__ import annotations

from typing import Any

import pytest

from conftest import make_need, make_request
from floodrelay.agent.forward_graph import GraphRunFailed, build_graph, run_forward
from floodrelay.agent.graph import Pipeline, RunState
from floodrelay.agent.nodes.dedupe import DuplicateVerdict
from floodrelay.agent.nodes.match import MatchResult
from floodrelay.models.common import Confidence, GeoPoint
from floodrelay.store.decisions_repo import DecisionsRepo
from floodrelay.store.requests_repo import RequestsRepo
from floodrelay.store.resources_repo import ResourcesRepo
from floodrelay.store.table import Table

NOWSHERA = (34.0151, 71.9747)


def _placed(score: float) -> GeoPoint:
    return GeoPoint(
        lat=NOWSHERA[0],
        lon=NOWSHERA[1],
        label="Pir Sabak, Nowshera District",
        source="nominatim",
        confidence=Confidence(score=score, reason="test fixture"),
    )


def _duplicate(score: float) -> DuplicateVerdict:
    return DuplicateVerdict(candidate_id="r_other", score=score, reason="test fixture")


def _match(*, contends: list[str] | None = None) -> MatchResult:
    return MatchResult(
        resource=None,
        distance_m=None,
        eta_min=None,
        reason="test fixture",
        contends_with=contends or [],
    )


class StubPipeline:
    """A pipeline whose nodes do nothing but record and set state.

    It carries the same `_run_*` surface `build_graph` binds to, so the graph
    under test is the real one -- only the work inside each node is replaced.
    """

    def __init__(
        self,
        state: RunState,
        *,
        geo_scores: list[float] | None = None,
        dedupe_scores: list[float] | None = None,
        contends: list[str] | None = None,
    ) -> None:
        self.state = state
        self.order: list[str] = []
        # One entry per visit, so a retry can return a better answer than the
        # first attempt did.
        self.geo_scores = list(geo_scores or [0.82])
        self.dedupe_scores = list(dedupe_scores or [0.0])
        self.contends = contends or []

    def _record(self, name: str) -> None:
        self.order.append(name)
        self.state.visited.append(name)

    def _run_extract(self, state: RunState, extra_context: str | None = None) -> None:
        self._record("extract")
        state.request.need = make_need(kind="rescue", raw_location_text="Pir Sabaq")
        if extra_context:
            state.notes.append("extract saw retry context")

    def _run_geolocate(self, state: RunState) -> None:
        self._record("geolocate")
        state.request.geo_attempts += 1
        score = self.geo_scores[min(state.request.geo_attempts - 1, len(self.geo_scores) - 1)]
        state.request.location = _placed(score) if score > 0 else None

    def _run_dedupe(self, state: RunState) -> None:
        self._record("dedupe")
        visit = self.order.count("dedupe")
        score = self.dedupe_scores[min(visit - 1, len(self.dedupe_scores) - 1)]
        state.duplicate = _duplicate(score)

    def _run_triage(self, state: RunState) -> None:
        self._record("triage")
        state.request.urgency = 0.7

    def _run_match(self, state: RunState) -> None:
        self._record("match")
        state.match_result = _match(contends=self.contends)

    def _run_gate(self, state: RunState) -> None:
        self._record("gate")
        state.halted_at = "gate"


def _drive(**kwargs: Any) -> tuple[StubPipeline, RunState]:
    state = RunState(request=make_request("r_1"))
    pipeline = StubPipeline(state, **kwargs)
    run_forward(pipeline, state, state.request)
    return pipeline, state


# --- the straight line ------------------------------------------------------


def test_a_clean_request_walks_the_whole_graph_once() -> None:
    pipeline, state = _drive()
    assert pipeline.order == ["extract", "geolocate", "dedupe", "triage", "match", "gate"]
    assert state.halted_at == "gate"


# --- the retry edge ---------------------------------------------------------


def test_a_weak_location_sends_control_back_to_extract() -> None:
    """geolocate -> extract, once, and the second answer is used."""
    pipeline, state = _drive(geo_scores=[0.30, 0.82])
    assert pipeline.order == [
        "extract", "geolocate", "extract", "geolocate", "dedupe", "triage", "match", "gate",
    ]
    assert "extract saw retry context" in state.notes


def test_the_retry_happens_at_most_once() -> None:
    """A second failure goes to the gate rather than looping again."""
    pipeline, _state = _drive(geo_scores=[0.30, 0.30])
    assert pipeline.order == [
        "extract", "geolocate", "extract", "geolocate", "dedupe", "triage", "match", "gate",
    ]
    assert pipeline.order.count("extract") == 2, "the graph looped more than once"


def test_a_request_with_no_location_at_all_still_reaches_the_gate() -> None:
    pipeline, _state = _drive(geo_scores=[0.0, 0.0])
    assert pipeline.order[-1] == "gate"


# --- the duplicate halt -----------------------------------------------------


def test_a_duplicate_stops_the_run_at_dedupe() -> None:
    """No edge out of dedupe is satisfied, so the graph simply ends."""
    pipeline, _state = _drive(dedupe_scores=[0.95])
    assert pipeline.order == ["extract", "geolocate", "dedupe"]


# --- the dedupe/match handback ---------------------------------------------


def test_a_resource_conflict_hands_back_to_dedupe_before_the_gate() -> None:
    pipeline, _state = _drive(contends=["r_other"])
    assert pipeline.order == [
        "extract", "geolocate", "dedupe", "triage", "match", "dedupe", "gate",
    ]


def test_the_handback_does_not_re_run_triage_and_match() -> None:
    """The second dedupe goes to the gate, not back around the loop."""
    pipeline, _state = _drive(contends=["r_other"])
    assert pipeline.order.count("triage") == 1
    assert pipeline.order.count("match") == 1


def test_a_duplicate_found_on_the_handback_stops_before_the_gate() -> None:
    pipeline, _state = _drive(contends=["r_other"], dedupe_scores=[0.0, 0.95])
    assert pipeline.order == ["extract", "geolocate", "dedupe", "triage", "match", "dedupe"]
    assert "gate" not in pipeline.order


# --- the ceiling ------------------------------------------------------------


def test_a_runaway_graph_raises_rather_than_stopping_quietly() -> None:
    """The SDK marks an over-budget run FAILED and returns; we must not.

    A forward pass that stopped halfway without raising would leave the request
    in `processing` with no card behind it -- the one state the console's colour
    scheme promises cannot happen.
    """

    class NeverSettles(StubPipeline):
        def _run_geolocate(self, state: RunState) -> None:
            self._record("geolocate")
            # Never advance the attempt counter, so the retry edge stays live.
            state.request.location = None

    state = RunState(request=make_request("r_1"))
    pipeline = NeverSettles(state)
    with pytest.raises(GraphRunFailed, match="without reaching a terminal node"):
        run_forward(pipeline, state, state.request)


# --- the graph is a real Strands graph -------------------------------------


def test_the_graph_is_built_with_the_sdk_and_carries_the_cycles() -> None:
    """Guards against this quietly becoming a hand-rolled executor again."""
    from strands.multiagent.graph import Graph

    state = RunState(request=make_request("r_1"))
    graph = build_graph(StubPipeline(state), state)

    assert isinstance(graph, Graph)
    assert set(graph.nodes) == {"extract", "geolocate", "dedupe", "triage", "match", "gate"}

    edges = {(e.from_node.node_id, e.to_node.node_id) for e in graph.edges}
    assert ("geolocate", "extract") in edges, "the retry edge is missing"
    assert ("match", "dedupe") in edges, "the dedupe handback is missing"
    assert not [e for e in graph.edges if e.from_node.node_id == "gate"], (
        "gate must be terminal: the run has to end when a human is asked"
    )

    conditional = {(e.from_node.node_id, e.to_node.node_id) for e in graph.edges if e.condition}
    assert ("geolocate", "extract") in conditional
    assert ("match", "dedupe") in conditional


# --- and the real pipeline, not just the stub -------------------------------


def test_the_real_pipeline_runs_through_the_graph(table: Table) -> None:
    """End to end on the actual nodes, model off, to catch a wiring mismatch."""
    pipeline = Pipeline(
        requests=RequestsRepo(table),
        resources=ResourcesRepo(table),
        decisions=DecisionsRepo(table),
        use_model=False,
    )
    request = make_request("r_1", raw_text="chhat par phanse hain, pani barh raha hai")
    state = pipeline.run(request)

    # No location is extractable with the model off, so this is the two-attempt
    # path: extract, geolocate, extract, geolocate, then straight on.
    assert state.visited[:4] == ["extract", "geolocate", "extract", "geolocate"]
    assert state.visited[-1] == "gate"
    assert state.card is not None
    assert state.request.status == "needs_decision"
