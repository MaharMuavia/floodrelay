"""The AgentCore Runtime service contract.

AgentCore requires `POST /invocations` and `GET /ping` on port 8080. A container
that does not serve them is marked unhealthy and restarted, with the reason only
visible in CloudWatch -- which is a slow way to find out you shipped the wrong
paths. These tests pin the contract so it cannot drift, and they run offline.

They also pin the thing that must stay true wherever the agent is reached from:
an invocation cannot dispatch anybody.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from conftest import make_resource
from floodrelay.main import create_app
from floodrelay.services.pipeline import PipelineService, set_pipeline_service
from floodrelay.store.decisions_repo import DecisionsRepo
from floodrelay.store.requests_repo import RequestsRepo
from floodrelay.store.resources_repo import ResourcesRepo
from floodrelay.store.table import Table


@pytest.fixture
def client(table: Table) -> Iterator[TestClient]:
    set_pipeline_service(PipelineService(use_model=False))
    with TestClient(create_app()) as c:
        yield c
    set_pipeline_service(None)


# --- /ping ------------------------------------------------------------------


def test_ping_answers_the_documented_shape(client: TestClient) -> None:
    r = client.get("/ping")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"status": "Healthy"}


def test_ping_reports_healthy_when_nothing_is_running(client: TestClient) -> None:
    """Only real background work may report HealthyBusy.

    The runtime keeps a session alive while it sees HealthyBusy, so an idle
    container reporting busy would hold sessions open until MaxLifetime.
    """
    from floodrelay.services.pipeline import inflight

    assert inflight() == 0
    assert client.get("/ping").json()["status"] == "Healthy"


def test_ping_does_not_send_a_timestamp_that_moves_every_call(client: TestClient) -> None:
    """The contract warns that this stops the idle timeout ever firing."""
    first = client.get("/ping").json()
    second = client.get("/ping").json()
    assert "time_of_last_update" not in first
    assert first == second


# --- /invocations -----------------------------------------------------------


def test_invocations_accepts_the_documented_request_shape(client: TestClient) -> None:
    r = client.post("/invocations", json={"prompt": "chhat par phanse hain, Pir Sabaq"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success"
    assert body["request_id"].startswith("r_")
    assert isinstance(body["response"], str)


def test_an_invocation_runs_the_same_graph_the_console_runs(client: TestClient) -> None:
    """Not a separate agent path that could drift from the tested one."""
    body = client.post("/invocations", json={"prompt": "pani ghar mein aa gaya hai"}).json()
    assert body["nodes_visited"][0] == "extract"
    assert body["nodes_visited"][-1] == "gate"


def test_an_invocation_stops_at_the_gate_and_says_so(client: TestClient) -> None:
    body = client.post("/invocations", json={"prompt": "chhat par phanse hain"}).json()
    assert body["awaiting_human"] is True
    assert body["decision"]["kind"]
    assert body["decision"]["heading"]


def test_an_invocation_never_dispatches(client: TestClient, table: Table) -> None:
    """The invariant, restated at the runtime boundary."""
    ResourcesRepo(table).save(make_resource("res_boat_1"))
    body = client.post(
        "/invocations", json={"prompt": "chhat par phanse hain, Pir Sabaq, boat bhejo"}
    ).json()

    assert body["dispatched"] is False
    assert all(r.status != "dispatched" for r in RequestsRepo(table).list_all())
    assert all(r.status != "assigned" for r in ResourcesRepo(table).list_all())


def test_the_runtime_surface_is_exactly_the_two_documented_paths(
    client: TestClient,
) -> None:
    """No third route quietly joins the runtime's tag.

    Approval is a coordinator's act in the console, not something posted to the
    runtime, so the only state-changing path the runtime exposes must be
    `/invocations`.
    """
    paths = client.get("/openapi.json").json()["paths"]
    tagged = {
        (path, method.upper())
        for path, methods in paths.items()
        for method, spec in methods.items()
        if "agentcore" in spec.get("tags", [])
    }
    assert tagged == {("/invocations", "POST"), ("/ping", "GET")}


def test_an_empty_prompt_is_rejected(client: TestClient) -> None:
    assert client.post("/invocations", json={"prompt": ""}).status_code == 422
    assert client.post("/invocations", json={"prompt": "   "}).status_code == 422


def test_an_oversized_prompt_is_rejected(client: TestClient) -> None:
    assert client.post("/invocations", json={"prompt": "x" * 4001}).status_code == 422


def test_a_missing_prompt_is_rejected(client: TestClient) -> None:
    assert client.post("/invocations", json={}).status_code == 422


def test_an_unknown_channel_falls_back_rather_than_failing(client: TestClient) -> None:
    r = client.post("/invocations", json={"prompt": "help", "channel": "carrier-pigeon"})
    assert r.status_code == 200


def test_a_failed_run_still_leaves_a_card_behind(client: TestClient, table: Table) -> None:
    """A 500 from the runtime must not mean a request nobody can answer."""
    import floodrelay.agent.nodes.extract as extract_mod

    set_pipeline_service(PipelineService(use_model=True))

    def boom(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("All connection attempts failed")

    original = extract_mod.run
    extract_mod.run = boom  # type: ignore[assignment]
    try:
        r = client.post("/invocations", json={"prompt": "chhat par phanse hain"})
    finally:
        extract_mod.run = original  # type: ignore[assignment]

    assert r.status_code == 500
    assert "decision card" in r.json()["detail"]

    cards = DecisionsRepo(table).list_open()
    assert cards, "the runtime returned 500 with nothing for a coordinator to answer"
    assert cards[0].kind == "processing_failed"
    assert all(not o.is_dispatch for o in cards[0].options)
