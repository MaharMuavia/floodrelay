"""API smoke tests.

The pipeline is run with `use_model=False` throughout: these tests are about the
HTTP surface -- validation, status codes, response shapes -- not about model
quality, and a suite that needs a 7B model on CPU is a suite nobody runs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from conftest import make_request
from floodrelay.config import get_settings
from floodrelay.main import create_app
from floodrelay.services.pipeline import PipelineService, set_pipeline_service
from floodrelay.store.requests_repo import RequestsRepo
from floodrelay.store.table import Table


@pytest.fixture
def client(table: Table) -> Iterator[TestClient]:
    set_pipeline_service(PipelineService(use_model=False))
    with TestClient(create_app()) as c:
        yield c
    set_pipeline_service(None)


# --- ops --------------------------------------------------------------------


def test_healthz_reports_store_and_models(client: TestClient) -> None:
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["checks"]["store"] == "ok"
    assert {"provider", "heavy", "light"} <= body["models"].keys()


def test_healthz_is_honest_about_photo_severity(client: TestClient) -> None:
    """Under a provider with no vision model, /healthz must say so."""
    checks = client.get("/healthz").json()["checks"]
    assert "photo_severity" in checks


def test_root_and_docs_are_reachable(client: TestClient) -> None:
    assert client.get("/").status_code == 200
    assert client.get("/openapi.json").status_code == 200


# --- intake validation ------------------------------------------------------


def test_intake_accepts_a_request(client: TestClient) -> None:
    r = client.post("/intake", json={"channel": "whatsapp", "text": "chhat par hain, madad"})
    assert r.status_code == 202
    body = r.json()
    assert body["request_id"].startswith("r_")
    assert body["trace_id"]


@pytest.mark.parametrize("text", ["", "   ", None])
def test_intake_rejects_empty_text(client: TestClient, text: str | None) -> None:
    assert client.post("/intake", json={"text": text}).status_code == 422


def test_intake_rejects_an_unknown_channel(client: TestClient) -> None:
    r = client.post("/intake", json={"channel": "carrier_pigeon", "text": "help"})
    assert r.status_code == 422


def test_intake_rejects_absurdly_long_text(client: TestClient) -> None:
    assert client.post("/intake", json={"text": "x" * 5000}).status_code == 422


def test_bulk_is_capped_at_a_hundred(client: TestClient) -> None:
    items = [{"text": f"message {i}"} for i in range(101)]
    assert client.post("/intake/bulk", json={"items": items}).status_code == 422


def test_bulk_accepts_a_reasonable_batch(client: TestClient) -> None:
    items = [{"text": f"pani ghar mein aa gaya {i}"} for i in range(3)]
    r = client.post("/intake/bulk", json={"items": items})
    assert r.status_code == 202
    assert r.json()["accepted"] == 3


def test_paste_splits_into_separate_requests(client: TestClient) -> None:
    r = client.post("/intake/paste", json={"text": "first message\n\nsecond message"})
    assert r.status_code == 202
    assert r.json()["accepted"] == 2


def test_paste_rejects_an_empty_blob(client: TestClient) -> None:
    assert client.post("/intake/paste", json={"text": "   "}).status_code == 422


def test_photo_upload_rejects_a_non_image(client: TestClient) -> None:
    r = client.post(
        "/intake/photo",
        data={"text": "flooded street"},
        files={"photo": ("notes.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 415


# --- the webhook signature --------------------------------------------------
#
# This is a public URL that anybody on the internet can post help requests to.
# An unauthenticated one is a queue anybody can fill, and a queue anybody can
# fill is a coordinator whose real calls are buried under someone else's noise.
# So it fails closed: unsigned is refused, and unconfigured is refused too.

WEBHOOK_SECRET = "test-app-secret"


@pytest.fixture
def signed_client(table: Table, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client whose app has a webhook secret configured."""
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    get_settings.cache_clear()
    set_pipeline_service(PipelineService(use_model=False))
    with TestClient(create_app()) as c:
        yield c
    set_pipeline_service(None)
    get_settings.cache_clear()


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_a_correctly_signed_webhook_is_accepted(signed_client: TestClient) -> None:
    body = json.dumps({"Body": "chhat par phanse hain", "From": "+920000000"}).encode()
    r = signed_client.post(
        "/intake/webhook",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )
    assert r.status_code == 202, r.text
    assert r.json()["request_id"].startswith("r_")
    # Still honest about what remains unverified: the payload shape.
    assert "not verified" in r.json()["note"]


def test_an_unsigned_webhook_is_refused(signed_client: TestClient) -> None:
    body = json.dumps({"Body": "chhat par phanse hain"}).encode()
    r = signed_client.post(
        "/intake/webhook", content=body, headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 401


def test_a_webhook_signed_with_the_wrong_secret_is_refused(signed_client: TestClient) -> None:
    body = json.dumps({"Body": "chhat par phanse hain"}).encode()
    r = signed_client.post(
        "/intake/webhook",
        content=body,
        headers={
            "X-Hub-Signature-256": _sign(body, "not-the-secret"),
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 401


def test_a_signature_for_a_different_body_is_refused(signed_client: TestClient) -> None:
    """The replay case: a real signature lifted onto tampered content."""
    signed = json.dumps({"Body": "water in the street"}).encode()
    tampered = json.dumps({"Body": "twelve people on a roof, send the boat"}).encode()
    r = signed_client.post(
        "/intake/webhook",
        content=tampered,
        headers={"X-Hub-Signature-256": _sign(signed), "Content-Type": "application/json"},
    )
    assert r.status_code == 401


@pytest.mark.parametrize("header", ["", "deadbeef", "sha1=deadbeef", "sha256=", "sha256=zzz"])
def test_a_malformed_signature_header_is_refused(
    signed_client: TestClient, header: str
) -> None:
    body = json.dumps({"Body": "help"}).encode()
    r = signed_client.post(
        "/intake/webhook",
        content=body,
        headers={"X-Hub-Signature-256": header, "Content-Type": "application/json"},
    )
    assert r.status_code == 401


def test_the_webhook_is_off_entirely_when_no_secret_is_configured(client: TestClient) -> None:
    """Fail closed. An unconfigured public webhook accepts nothing at all."""
    body = json.dumps({"Body": "chhat par phanse hain"}).encode()
    r = client.post(
        "/intake/webhook",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )
    assert r.status_code == 503
    assert "WEBHOOK_SECRET" in r.json()["detail"]


def test_a_signed_payload_with_no_message_body_is_rejected(signed_client: TestClient) -> None:
    body = json.dumps({"foo": "bar"}).encode()
    r = signed_client.post(
        "/intake/webhook",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )
    assert r.status_code == 422


def test_a_signed_payload_that_is_not_json_is_rejected(signed_client: TestClient) -> None:
    body = b"not json at all"
    r = signed_client.post(
        "/intake/webhook",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"},
    )
    assert r.status_code == 422


# --- board ------------------------------------------------------------------


def test_board_is_empty_before_anything_arrives(client: TestClient) -> None:
    body = client.get("/board").json()
    assert body["requests"] == []
    assert body["counts"]["total"] == 0
    assert body["resources"], "the seeded roster should be installed at startup"


def test_board_returns_requests_most_urgent_first(client: TestClient, table: Table) -> None:
    repo = RequestsRepo(table)
    repo.save(make_request("r_low", urgency=0.20, status="new"))
    repo.save(make_request("r_high", urgency=0.95, status="new"))

    ids = [row["id"] for row in client.get("/board").json()["requests"]]
    assert ids.index("r_high") < ids.index("r_low")


def test_board_filters_by_status(client: TestClient, table: Table) -> None:
    repo = RequestsRepo(table)
    repo.save(make_request("r_1", status="new", urgency=0.5))
    repo.save(make_request("r_2", status="dispatched", urgency=0.5))

    rows = client.get("/board", params={"status": "dispatched"}).json()["requests"]
    assert [r["id"] for r in rows] == ["r_2"]


def test_request_detail_includes_the_urgency_breakdown(client: TestClient, table: Table) -> None:
    RequestsRepo(table).save(make_request("r_1", urgency=0.5))
    body = client.get("/requests/r_1").json()
    assert set(body["urgency_breakdown"]) >= {"kind", "vulnerability", "photo", "recency", "total"}
    assert body["urgency_weights"]["kind"] == 0.40


def test_unknown_request_is_a_404(client: TestClient) -> None:
    assert client.get("/requests/r_nope").status_code == 404


def test_the_urgency_formula_is_published(client: TestClient) -> None:
    """The UI shows this on hover; it must come from the server, not be hard-coded."""
    body = client.get("/urgency/formula").json()
    assert sum(body["weights"].values()) == pytest.approx(1.0)
    assert body["kind_weights"]["rescue"] == 1.0
    assert "never by the language model" in body["note"]


def test_heatmap_returns_cells(client: TestClient) -> None:
    body = client.get("/map/heatmap").json()
    assert "cells" in body


# --- decisions ---------------------------------------------------------------


def test_no_open_decisions_initially(client: TestClient) -> None:
    assert client.get("/decisions").json()["decisions"] == []


def test_resolving_an_unknown_decision_is_a_404(client: TestClient) -> None:
    r = client.post("/decisions/d_nope/resolve", json={"option_id": "A"})
    assert r.status_code == 404


# --- audit and demo ----------------------------------------------------------


def test_audit_starts_empty_and_says_it_is_append_only(client: TestClient) -> None:
    body = client.get("/audit").json()
    assert body["events"] == []
    assert "Append-only" in body["note"]


def test_demo_info_declares_the_data_synthetic(client: TestClient) -> None:
    body = client.get("/demo/info").json()
    assert body["synthetic"] is True
    assert body["requests"] == 40, "the seed set should hold 40 messages"
    assert "No real people" in body["note"]


def test_demo_reset_reinstalls_the_roster(client: TestClient) -> None:
    body = client.post("/demo/reset").json()
    assert body["resources"] == 7


# --- internal routes (the scheduler's entry point) ---------------------------
#
# These must work with DEMO_MODE off. An earlier version only exposed rescan
# under /demo, which meant the scheduled re-evaluation would have been silently
# dead in exactly the configuration that needs it.


def test_rescan_is_reachable_outside_demo_mode(client: TestClient) -> None:
    r = client.post("/internal/rescan")
    assert r.status_code == 200
    assert "rescored" in r.json()


def test_rescan_is_idempotent(client: TestClient, table: Table) -> None:
    RequestsRepo(table).save(make_request("r_1", urgency=0.5, status="new"))
    first = client.post("/internal/rescan").json()
    second = client.post("/internal/rescan").json()
    assert first["rescored"] >= 0
    assert second["rescored"] == 0, "a second pass should find nothing left to change"


def test_the_readiness_probe_needs_no_store(client: TestClient) -> None:
    assert client.get("/internal/ready").json()["ready"] is True


def test_the_internal_token_is_enforced_when_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unset by default; when set, it must actually be checked."""
    from floodrelay.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "internal_token", "s3cret", raising=False)

    assert client.post("/internal/rescan").status_code == 401
    assert client.post("/internal/rescan", headers={"X-Internal-Token": "wrong"}).status_code == 401
    ok = client.post("/internal/rescan", headers={"X-Internal-Token": "s3cret"})
    assert ok.status_code == 200


def test_the_demo_rescan_alias_is_refused_when_demo_mode_is_off(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from floodrelay.config import get_settings

    monkeypatch.setattr(get_settings(), "demo_mode", False, raising=False)
    assert client.post("/demo/rescan").status_code == 403
    # ...but the scheduler's route still works, which is the whole point.
    assert client.post("/internal/rescan").status_code == 200
