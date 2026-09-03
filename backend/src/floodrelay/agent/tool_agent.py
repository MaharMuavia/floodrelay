"""The one place a tool-calling `strands.Agent` is built.

`nodes/_llm.py` builds a bare completion agent for language tasks. This module
builds the other kind: an agent that is handed a set of `@tool` functions and
left to choose which of them to call. Every such agent carries the same three
typed hooks, always, because the hooks are the safety story:

* `HumanGateHook`  -- refuses a dispatch-class tool call without a resolved,
                      unspent `DecisionCard`. This is the whole product.
* `AuditLogHook`   -- writes every tool call and model call to the trail.
* `PIIRedactionHook` -- nothing reaches the model unredacted.

A fourth, `ToolTraceHook`, is local to this module and carries no policy: it
records what the model actually chose, in order, so that

  1. the nodes can compute their numbers from the values the tools really
     returned rather than from the model's prose about them, and
  2. the console can show a coordinator which tools ran.

That second point matters more than it sounds. "The agent called the geocoder"
is a claim, and a claim about an autonomous system should be checkable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..store.audit_repo import AuditRepo
from ..store.decisions_repo import DecisionsRepo
from .hooks.audit_log import AuditLogHook
from .hooks.human_gate import HumanGateHook
from .hooks.pii_redaction import PIIRedactionHook
from .models import Role, get_model, tool_calling_active


@dataclass(frozen=True)
class ToolCall:
    """One tool invocation the model chose to make."""

    name: str
    input: dict[str, Any]
    output: Any = None
    error: str | None = None
    latency_ms: int | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def summary(self) -> str:
        """A one-line description for the activity feed."""
        if self.error:
            return f"{self.name} failed: {self.error}"
        args = ", ".join(f"{k}={v!r}" for k, v in list(self.input.items())[:3])
        return f"{self.name}({args})"


def _decode(result: Any) -> Any:
    """Pull the payload back out of a Strands `ToolResult`.

    `@tool` serialises a dict return to a JSON string in a text content block.
    The nodes want the dict back, so they can read the candidate list the
    geocoder actually returned instead of re-parsing the model's summary of it.
    """
    if not isinstance(result, dict):
        return result
    content = result.get("content")
    if not isinstance(content, list):
        return result
    for block in content:
        if not isinstance(block, dict):
            continue
        if "json" in block:
            return block["json"]
        text = block.get("text")
        if isinstance(text, str):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    return result


@dataclass
class ToolTraceHook:
    """Records which tools the model chose, in order. No policy, just evidence."""

    calls: list[ToolCall] = field(default_factory=list)

    def register_hooks(self, registry: Any, **kwargs: Any) -> None:
        from strands.hooks import AfterToolCallEvent

        registry.add_callback(AfterToolCallEvent, self.after_tool_call)

    def after_tool_call(self, event: Any) -> None:
        tool_use = getattr(event, "tool_use", None) or {}
        exception = getattr(event, "exception", None)
        duration = getattr(event, "duration", None)
        raw = getattr(event, "result", None)

        error: str | None = None
        if exception is not None:
            error = f"{exception.__class__.__name__}: {exception}"
        elif isinstance(raw, dict) and raw.get("status") == "error":
            error = str(_decode(raw))

        self.calls.append(
            ToolCall(
                name=str(tool_use.get("name", "")),
                input=dict(tool_use.get("input") or {}),
                output=_decode(raw),
                error=error,
                latency_ms=int(duration * 1000) if duration else None,
            )
        )

    # --- what the nodes ask it --------------------------------------------

    def called(self, name: str) -> bool:
        return any(c.name == name and c.ok for c in self.calls)

    def results_for(self, name: str) -> list[Any]:
        """Every successful payload from one tool, in call order."""
        return [c.output for c in self.calls if c.name == name and c.ok]

    def first_result(self, name: str) -> Any | None:
        results = self.results_for(name)
        return results[0] if results else None

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.calls]


def build_agent(
    *,
    role: Role,
    tools: list[Any],
    system_prompt: str,
    request_id: str | None = None,
    trace_id: str | None = None,
    node: str | None = None,
    decisions: DecisionsRepo | None = None,
    audit: AuditRepo | None = None,
) -> tuple[Any, ToolTraceHook]:
    """Build a tool-calling agent with the full hook set attached.

    Returns the agent and the trace hook, because the caller almost always needs
    to read what the model chose after the turn ends.
    """
    from strands import Agent

    trace = ToolTraceHook()
    hooks: list[Any] = [
        # Order is deliberate: redact on the way in, refuse before the call,
        # record after it.
        PIIRedactionHook(),
        HumanGateHook(decisions),
        AuditLogHook(audit or AuditRepo(), request_id=request_id, trace_id=trace_id, node=node),
        trace,
    ]

    agent = Agent(
        model=get_model(role),
        tools=tools,
        system_prompt=system_prompt,
        hooks=hooks,
        callback_handler=None,
    )
    return agent, trace


def run_agent(
    *,
    role: Role,
    tools: list[Any],
    system_prompt: str,
    user: str,
    request_id: str | None = None,
    trace_id: str | None = None,
    node: str | None = None,
    decisions: DecisionsRepo | None = None,
    audit: AuditRepo | None = None,
    invocation_state: dict[str, Any] | None = None,
) -> tuple[str, ToolTraceHook]:
    """One tool-calling turn. Returns the final text and the trace of tool calls.

    `invocation_state` is how a `decision_card_id` reaches the human gate. A
    turn started without one cannot dispatch anything, which is the correct
    default for every node in the forward pass -- the gate has not run yet.
    """
    agent, trace = build_agent(
        role=role,
        tools=tools,
        system_prompt=system_prompt,
        request_id=request_id,
        trace_id=trace_id,
        node=node,
        decisions=decisions,
        audit=audit,
    )
    result = agent(user, invocation_state=invocation_state or {})
    return str(result), trace


def active() -> bool:
    """Whether the configured provider can run the loop above at all."""
    return tool_calling_active()
