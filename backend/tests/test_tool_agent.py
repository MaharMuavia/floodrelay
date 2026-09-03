"""The gate on the real agent path.

`test_human_gate.py` proves `enforce_gate` refuses. This file proves the thing
that actually has to be true: that when a genuine `strands.Agent` decides on its
own to call a dispatch-class tool, the refusal happens *inside the agent loop*,
before the tool function runs. A gate that only works when Python calls it is
not a gate on an autonomous agent.

The model is a scripted fake, so the whole file runs offline with no provider,
no credentials and no network. What is real is everything after it: a real
`Agent`, the real `@tool` functions, the real hook registry, and the real
`HumanGateHook` on the real `BeforeToolCallEvent`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterable
from typing import Any

import pytest
from strands.models import Model

from conftest import make_request, make_resource
from floodrelay.agent.hooks.human_gate import caused_by_gate
from floodrelay.agent.tool_agent import ToolTraceHook, build_agent
from floodrelay.models.decision import DecisionCard, DecisionOption
from floodrelay.store.audit_repo import AuditRepo
from floodrelay.store.decisions_repo import DecisionsRepo
from floodrelay.store.requests_repo import RequestsRepo
from floodrelay.store.resources_repo import ResourcesRepo
from floodrelay.store.table import Table


class ScriptedToolCallingModel(Model):
    """A model that emits a fixed sequence of turns.

    Each turn is either `("tool", name, input_dict)` or `("text", body)`. That is
    the whole contract with the SDK: the event shapes below are the streaming
    protocol Strands parses, so an agent driven by this is following exactly the
    code path a Bedrock or Anthropic model drives. It subclasses the SDK's own
    `Model` so the agent gets a real provider, not a duck-typed stand-in.
    """

    def __init__(self, turns: list[tuple[str, Any, Any]]) -> None:
        self.turns = list(turns)
        self.calls = 0

    # --- the Model interface ---------------------------------------------

    def update_config(self, **model_config: Any) -> None:
        return None

    def get_config(self) -> dict[str, Any]:
        return {"model_id": "scripted-test-model"}

    def structured_output(
        self, output_model: Any, prompt: Any, system_prompt: str | None = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError("the scripted model does not do structured output")

    def stream(
        self,
        messages: Any,
        tool_specs: Any = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[dict[str, Any]]:
        turn = self.turns[min(self.calls, len(self.turns) - 1)]
        self.calls += 1
        return self._emit(turn)

    async def _emit(self, turn: tuple[str, Any, Any]) -> AsyncGenerator[dict[str, Any], None]:
        kind = turn[0]
        yield {"messageStart": {"role": "assistant"}}

        if kind == "tool":
            _, name, payload = turn
            yield {
                "contentBlockStart": {
                    "start": {"toolUse": {"toolUseId": f"tu_{self.calls}", "name": name}},
                    "contentBlockIndex": 0,
                }
            }
            yield {
                "contentBlockDelta": {
                    "delta": {"toolUse": {"input": json.dumps(payload)}},
                    "contentBlockIndex": 0,
                }
            }
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            _, body, _ = turn
            yield {"contentBlockStart": {"start": {}, "contentBlockIndex": 0}}
            yield {"contentBlockDelta": {"delta": {"text": body}, "contentBlockIndex": 0}}
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
            yield {"messageStop": {"stopReason": "end_turn"}}

        yield {
            "metadata": {
                "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
                "metrics": {"latencyMs": 1},
            }
        }


def _agent(
    turns: list[tuple[str, Any, Any]],
    tools: list[Any],
    table: Table,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, ToolTraceHook, ScriptedToolCallingModel]:
    """A real Agent with the real hook set, over a scripted model."""
    model = ScriptedToolCallingModel(turns)
    monkeypatch.setattr(
        "floodrelay.agent.tool_agent.get_model", lambda role, settings=None: model
    )
    agent, trace = build_agent(
        role="light",
        tools=tools,
        system_prompt="You coordinate flood relief.",
        request_id="r_03",
        decisions=DecisionsRepo(table),
        audit=AuditRepo(table),
    )
    return agent, trace, model


def _approved_card(decisions: DecisionsRepo) -> DecisionCard:
    card = decisions.save(
        DecisionCard(
            id="d_live",
            kind="resource_conflict",
            request_ids=["r_03"],
            heading="One boat, two calls.",
            reasoning="More people and higher water at r_03.",
            options=[
                DecisionOption(
                    id="A",
                    label="Send the boat to r_03",
                    request_id="r_03",
                    resource_id="res_boat_1",
                    is_dispatch=True,
                ),
                DecisionOption(id="HOLD", label="Neither - hold", is_dispatch=False),
            ],
        )
    )
    decisions.resolve(card.id, "A")
    return card


# --- the refusal, through the agent loop -----------------------------------


def test_an_agent_that_decides_to_dispatch_is_refused_by_the_hook(
    table: Table, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The headline case, on the path that matters.

    The model is not asked to dispatch and is given no approval. It tries
    anyway, which is exactly the failure this product exists to make impossible.
    """
    from floodrelay.agent.tools.roster import roster_assign

    ResourcesRepo(table).save(make_resource("res_boat_1"))
    RequestsRepo(table).save(make_request("r_03", status="matched"))

    agent, _trace, _model = _agent(
        [("tool", "roster_assign", {"request_id": "r_03", "resource_id": "res_boat_1"})],
        [roster_assign],
        table,
        monkeypatch,
    )

    # The SDK wraps whatever the hook raised, so the type is asserted on the
    # cause chain below rather than by pytest.raises.
    with pytest.raises(Exception) as caught:
        agent("Someone is on a roof at Pir Sabaq.")

    violation = caused_by_gate(caught.value)
    assert violation is not None, f"the run failed for some other reason: {caught.value!r}"
    assert "no decision_card_id" in str(violation)

    # And nothing happened to the world on the way out.
    assert RequestsRepo(table).require("r_03").status == "matched"
    assert ResourcesRepo(table).require("res_boat_1").status == "available"


def test_the_refusal_happens_before_the_tool_body_runs(
    table: Table, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`BeforeToolCallEvent`, not a check inside the function.

    If the hook let the call through and only the in-function gate stopped it,
    the trace would show a failed call. It shows no call at all, because the
    tool was never entered.
    """
    from floodrelay.agent.tools.roster import roster_assign

    ResourcesRepo(table).save(make_resource("res_boat_1"))
    RequestsRepo(table).save(make_request("r_03", status="matched"))

    agent, trace, _model = _agent(
        [("tool", "roster_assign", {"request_id": "r_03", "resource_id": "res_boat_1"})],
        [roster_assign],
        table,
        monkeypatch,
    )

    # The SDK wraps whatever the hook raised, so the type is asserted on the
    # cause chain below rather than by pytest.raises.
    with pytest.raises(Exception) as caught:
        agent("Send the boat.")

    assert caused_by_gate(caught.value) is not None
    assert trace.calls == [], "the tool body ran; the hook did not refuse first"


def test_an_approval_for_another_request_does_not_let_the_agent_through(
    table: Table, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real card, resolved by a real human, for somebody else."""
    from floodrelay.agent.tools.roster import roster_assign

    decisions = DecisionsRepo(table)
    ResourcesRepo(table).save(make_resource("res_boat_1"))
    RequestsRepo(table).save(make_request("r_28", status="matched"))
    _approved_card(decisions)  # covers r_03, not r_28

    agent, _trace, _model = _agent(
        [("tool", "roster_assign", {"request_id": "r_28", "resource_id": "res_boat_1"})],
        [roster_assign],
        table,
        monkeypatch,
    )

    # The SDK wraps whatever the hook raised, so the type is asserted on the
    # cause chain below rather than by pytest.raises.
    with pytest.raises(Exception) as caught:
        agent("Send the boat to r_28.", invocation_state={"decision_card_id": "d_live"})

    violation = caused_by_gate(caught.value)
    assert violation is not None
    assert "covers request r_03" in str(violation)


# --- the tools that are not gated ------------------------------------------


def test_the_agent_really_does_call_an_ungated_tool(
    table: Table, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the claim: the hooks do not block ordinary work.

    This is the shape of a live run -- the model chooses `roster_search`, the
    function actually executes, and the result comes back into the loop.
    """
    from floodrelay.agent.tools.roster import roster_search

    ResourcesRepo(table).save(make_resource("res_boat_1"))
    monkeypatch.setattr("floodrelay.agent.tools.roster.ResourcesRepo", lambda: ResourcesRepo(table))

    agent, trace, _model = _agent(
        [
            ("tool", "roster_search", {"need_kind": "rescue", "lat": 34.0151, "lon": 71.9747}),
            ("text", "The nearest boat is Rescue boat 1.", None),
        ],
        [roster_search],
        table,
        monkeypatch,
    )

    result = agent("What is available for a rescue at Pir Sabaq?")

    assert trace.names == ["roster_search"]
    call = trace.calls[0]
    assert call.ok
    assert call.output["count"] == 1
    assert call.output["resources"][0]["id"] == "res_boat_1"
    assert "Rescue boat 1" in str(result)


def test_the_audit_trail_records_the_agents_tool_call(
    table: Table, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool call the model made on its own still lands in the trail."""
    from floodrelay.agent.tools.roster import roster_search

    ResourcesRepo(table).save(make_resource("res_boat_1"))
    monkeypatch.setattr("floodrelay.agent.tools.roster.ResourcesRepo", lambda: ResourcesRepo(table))

    agent, _trace, _model = _agent(
        [
            ("tool", "roster_search", {"need_kind": "rescue"}),
            ("text", "One boat is available.", None),
        ],
        [roster_search],
        table,
        monkeypatch,
    )
    agent("What is available?")

    events = AuditRepo(table).list_recent(limit=50)
    tools_recorded = [e.tool for e in events]
    assert "roster_search" in tools_recorded, tools_recorded
    assert "model" in tools_recorded, "model calls are not being audited"
    recorded = next(e for e in events if e.tool == "roster_search")
    assert recorded.request_id == "r_03"


def test_pii_is_redacted_before_it_reaches_the_model(
    table: Table, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The redaction hook is registered on the same agent, and it fires."""
    from floodrelay.agent.tools.roster import roster_search

    agent, _trace, _model = _agent(
        [("text", "Understood.", None)],
        [roster_search],
        table,
        monkeypatch,
    )
    agent("Call Asif on 0300-1234567, he is on the roof.")

    sent = json.dumps(agent.messages)
    assert "0300-1234567" not in sent, "a phone number reached the model"
    assert "CALLER_1" in sent or "PERSON_1" in sent, sent
