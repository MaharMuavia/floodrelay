"""The Bedrock AgentCore Runtime service contract.

AgentCore does not call `/healthz` and it does not call `/intake`. It requires
two specific paths on port 8080, and a container that does not serve them is
marked unhealthy and restarted rather than being told why:

* `POST /invocations` -- the agent entry point. JSON in, JSON or SSE out.
* `GET /ping` -- health, answering `{"status": "Healthy" | "HealthyBusy"}`.

Verified against the AgentCore HTTP protocol contract, which also requires an
**ARM64** image. See `infra/agentcore/README.md`.

## What an invocation means here

This product is a console, not a chatbot, so `/invocations` is not a chat turn.
One invocation is **one help request arriving**: the prompt is the message, and
the response is what the pipeline concluded about it. That keeps a deployed
runtime and the local console running exactly the same code -- there is no
separate agent path that could drift from the one the tests cover.

## What it cannot do

Nothing here can dispatch. An invocation runs the forward graph, which halts at
`gate` and writes a `DecisionCard` for a person to answer. There is deliberately
no route in this file that resolves a card: approving a dispatch is a
coordinator's act in the console, not something reachable by posting JSON to the
runtime.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, HTTPException, status
from pydantic import BaseModel, Field

from ..services.pipeline import inflight
from .deps import PipelineDep

router = APIRouter(tags=["agentcore"])

MAX_PROMPT_CHARS = 4000


class InvocationIn(BaseModel):
    """The documented AgentCore request shape, plus the fields we can use."""

    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    channel: str | None = None


class Ping(BaseModel):
    status: Literal["Healthy", "HealthyBusy"]


@router.get("/ping", response_model=Ping, tags=["ops"])
def ping() -> Ping:
    """AgentCore's health probe.

    `HealthyBusy` is reported only while pipeline runs are actually queued or
    executing. The runtime keeps a session alive for as long as it sees that
    status, so reporting it when idle would hold sessions open until
    `MaxLifetime` and exhaust the session quota.

    `time_of_last_update` is deliberately omitted: the contract warns that a
    timestamp advancing on every ping reads as a continuous status change and
    stops the idle timeout from ever firing. With the field absent the platform
    tracks the transitions itself.
    """
    return Ping(status="HealthyBusy" if inflight() > 0 else "Healthy")


@router.post("/invocations", tags=["agentcore"])
def invocations(
    service: PipelineDep,
    payload: Annotated[InvocationIn, Body()],
) -> dict[str, Any]:
    """One help request, run through the same graph the console runs.

    Synchronous, unlike `/intake`. `/intake` returns 202 because a frightened
    person on a bad line should not wait on a model; an API caller invoking a
    runtime is asking what happened and has nowhere to receive an answer later.

    The response says plainly whether a human now has to answer something. That
    is the honest summary of what this agent does: it got as far as it is
    allowed to get on its own, and then it stopped.
    """
    text = payload.prompt.strip()
    if not text:
        raise HTTPException(status_code=422, detail="prompt must not be empty")

    channel = payload.channel if payload.channel in {"form", "whatsapp", "sms"} else "form"
    request = service.accept(text, channel=channel)  # type: ignore[arg-type]

    try:
        state = service.process(request)
    except Exception as exc:
        # `process` has already written a processing_failed card, so a
        # coordinator has something to answer. Report it rather than a bare 500.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"the run failed and was raised as a decision card for a human: "
                f"{exc.__class__.__name__}: {exc}"
            ),
        ) from exc

    need = state.request.need
    return {
        "status": "success",
        "response": state.explanation or "Processed.",
        "request_id": state.request.id,
        "trace_id": state.request.trace_id,
        "request_status": state.request.status,
        "urgency": state.request.urgency,
        "need_kind": need.kind if need else None,
        "location": (
            {
                "lat": state.request.location.lat,
                "lon": state.request.location.lon,
                "label": state.request.location.label,
                "confidence": state.request.location.confidence.score,
            }
            if state.request.location
            else None
        ),
        "nodes_visited": state.visited,
        "awaiting_human": state.card is not None,
        "decision": (
            {
                "id": state.card.id,
                "kind": state.card.kind,
                "heading": state.card.heading,
            }
            if state.card
            else None
        ),
        "dispatched": False,
    }
