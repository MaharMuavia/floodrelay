"""Audit hook.

Every tool call and every model call lands in the append-only trail with its
inputs, outputs, latency and token count. This is what the audit screen renders
and what makes a CloudWatch trace legible after the fact.

Inputs and outputs are stored as digests rather than verbatim payloads: short
values are kept readable, long ones are truncated with a sha256 prefix so two
large payloads can still be told apart. The trail is meant to be read by a human
asking "why did it do that", not to be a second copy of the database.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from ...models.audit import AuditEvent
from ...store.audit_repo import AuditRepo, digest


def new_audit_id() -> str:
    return f"a_{uuid.uuid4().hex[:10]}"


class AuditLogHook:
    """Records tool and model calls made by an agent."""

    def __init__(
        self,
        repo: AuditRepo | None = None,
        *,
        request_id: str | None = None,
        trace_id: str | None = None,
        node: str | None = None,
    ) -> None:
        self.repo = repo or AuditRepo()
        self.request_id = request_id
        self.trace_id = trace_id
        self.node = node
        self._model_started: float | None = None

    def register_hooks(self, registry: Any, **kwargs: Any) -> None:
        from strands.hooks import (
            AfterModelCallEvent,
            AfterToolCallEvent,
            BeforeModelCallEvent,
        )

        registry.add_callback(AfterToolCallEvent, self.after_tool_call)
        registry.add_callback(BeforeModelCallEvent, self.before_model_call)
        registry.add_callback(AfterModelCallEvent, self.after_model_call)

    # --- tools ------------------------------------------------------------

    def after_tool_call(self, event: Any) -> None:
        tool_use = getattr(event, "tool_use", None) or {}
        duration = getattr(event, "duration", None)
        exception = getattr(event, "exception", None)

        self.record(
            tool=tool_use.get("name"),
            input_value=tool_use.get("input"),
            output_value=getattr(event, "result", None),
            latency_ms=int(duration * 1000) if duration else None,
            error=f"{exception.__class__.__name__}: {exception}" if exception else None,
        )

    # --- model calls ------------------------------------------------------

    def before_model_call(self, event: Any) -> None:
        self._model_started = time.monotonic()

    def after_model_call(self, event: Any) -> None:
        latency = (
            int((time.monotonic() - self._model_started) * 1000)
            if self._model_started is not None
            else None
        )
        self._model_started = None

        tokens = None
        usage = getattr(getattr(event, "response", None), "usage", None)
        if usage is not None:
            total = getattr(usage, "totalTokens", None) or getattr(usage, "total_tokens", None)
            tokens = int(total) if total else None

        self.record(tool="model", latency_ms=latency, tokens=tokens)

    # --- the write --------------------------------------------------------

    def record(
        self,
        *,
        tool: str | None = None,
        node: str | None = None,
        input_value: Any = None,
        output_value: Any = None,
        latency_ms: int | None = None,
        tokens: int | None = None,
        error: str | None = None,
        actor: str = "agent",
        decision_card_id: str | None = None,
        request_id: str | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            id=new_audit_id(),
            actor=actor,  # type: ignore[arg-type]
            node=node or self.node,
            tool=tool,
            request_id=request_id or self.request_id,
            input_digest=digest(input_value),
            output_digest=digest(output_value),
            latency_ms=latency_ms,
            tokens=tokens,
            error=error,
            decision_card_id=decision_card_id,
            trace_id=self.trace_id,
        )
        return self.repo.append(event)
