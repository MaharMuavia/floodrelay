"""Pipeline orchestration.

The only caller of agent/graph.py. Everything that wants a request processed --
the intake routes, the demo replay, the periodic rescan -- goes through here, so
there is exactly one code path and no separate demo branch.

Runs happen on a worker thread. Model calls against a local 7B on CPU take
seconds, and an event loop blocked for seconds is a console that stops
streaming.
"""

from __future__ import annotations

import contextlib
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..agent.graph import Pipeline, RunState
from ..agent.hooks.audit_log import AuditLogHook
from ..agent.nodes import intake
from ..models.request import Channel, HelpRequest
from ..store.audit_repo import AuditRepo
from ..store.decisions_repo import DecisionsRepo
from ..store.requests_repo import RequestsRepo
from ..store.resources_repo import ResourcesRepo
from .events import get_bus

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="floodrelay-pipeline")
_lock = threading.Lock()

# Queued-or-running work, for the AgentCore /ping contract. The runtime treats a
# session reporting HealthyBusy as still active and keeps it alive, so this has
# to reflect real background work and nothing else -- reporting busy when idle
# would hold sessions open until MaxLifetime and burn the session quota.
_inflight_lock = threading.Lock()
_inflight_count = 0


def inflight() -> int:
    """How many pipeline runs are queued or executing right now."""
    with _inflight_lock:
        return _inflight_count


class PipelineService:
    def __init__(self, *, use_model: bool = True) -> None:
        self.use_model = use_model

    # --- building requests -------------------------------------------------

    def accept(
        self,
        text: str,
        *,
        channel: Channel = "form",
        photo_key: str | None = None,
    ) -> HelpRequest:
        """Redact, persist and queue a request. Returns immediately."""
        request = intake.build(text, channel=channel, photo_key=photo_key)
        RequestsRepo().save(request)
        get_bus().publish(
            {
                "type": "request_received",
                "request_id": request.id,
                "channel": channel,
                "trace_id": request.trace_id,
            }
        )
        return request

    # --- running -----------------------------------------------------------

    def _build(self) -> Pipeline:
        return Pipeline(
            requests=RequestsRepo(),
            resources=ResourcesRepo(),
            decisions=DecisionsRepo(),
            use_model=self.use_model,
            emit=get_bus().publish,
        )

    def process(self, request: HelpRequest) -> RunState:
        """Run the graph for one request, synchronously, with auditing."""
        audit = AuditLogHook(
            AuditRepo(), request_id=request.id, trace_id=request.trace_id
        )
        # Serialised: dedupe and contention both read the whole open set, and
        # two concurrent runs would race on it. Correctness beats throughput at
        # this scale -- a district produces tens of requests, not thousands.
        with _lock:
            try:
                state = self._build().run(request)
            except Exception as exc:
                detail = f"{exc.__class__.__name__}: {exc}"
                audit.record(node="pipeline", error=detail, actor="system")

                # A failed run must still hand the coordinator something they can
                # answer. Marking the request `needs_decision` without a card left
                # it amber in the queue with nothing behind it and no way forward.
                from ..agent.nodes.gate import processing_failed_card

                card = processing_failed_card(request, detail)
                DecisionsRepo().save(card)

                request.status = "needs_decision"
                RequestsRepo().save(request)
                get_bus().publish(
                    {"type": "run_failed", "request_id": request.id, "error": detail}
                )
                get_bus().publish(
                    {
                        "type": "decision_required",
                        "decision_id": card.id,
                        "kind": card.kind,
                        "request_ids": card.request_ids,
                    }
                )
                raise

        for node in state.visited:
            audit.record(node=node, tool=None, output_value={"visited": node})
        if state.card is not None:
            audit.record(
                node="gate",
                output_value={"decision": state.card.kind},
                decision_card_id=state.card.id,
            )
        return state

    def submit(self, request: HelpRequest) -> None:
        """Queue a run on the worker pool."""
        global _inflight_count
        with _inflight_lock:
            _inflight_count += 1
        _executor.submit(self._safe_process, request)

    def _safe_process(self, request: HelpRequest) -> None:
        global _inflight_count
        try:
            # Already audited and published inside `process`; swallowing here just
            # stops one bad request from killing the worker thread.
            with contextlib.suppress(Exception):
                self.process(request)
        finally:
            with _inflight_lock:
                _inflight_count -= 1

    # --- resuming ----------------------------------------------------------

    def resolve_decision(
        self, decision_id: str, option_id: str, *, note: str | None = None
    ) -> dict[str, Any]:
        """Record the coordinator's answer and re-enter the graph."""
        decisions = DecisionsRepo()
        card = decisions.resolve(decision_id, option_id, note=note)

        audit = AuditLogHook(AuditRepo(), trace_id=card.trace_id)
        audit.record(
            node="gate",
            tool="resolve_decision",
            actor="coordinator",
            input_value={"decision_id": decision_id, "option_id": option_id, "note": note},
            decision_card_id=card.id,
        )
        get_bus().publish(
            {
                "type": "decision_resolved",
                "decision_id": card.id,
                "option_id": option_id,
                "request_ids": card.request_ids,
            }
        )

        results: list[str] = []
        with _lock:
            pipeline = self._build()
            for request_id in card.request_ids:
                try:
                    state = pipeline.resume_after_decision(request_id, card)
                    results.append(f"{request_id}:{state.request.status}")
                except Exception as exc:
                    audit.record(
                        node="resume",
                        request_id=request_id,
                        error=f"{exc.__class__.__name__}: {exc}",
                        actor="system",
                    )
                    results.append(f"{request_id}:error")

        return {"decision_id": card.id, "option_id": option_id, "outcomes": results}

    # --- periodic ----------------------------------------------------------

    def rescan(self) -> dict[str, Any]:
        """Re-score open requests so recency decay keeps the board honest."""
        from .scoring import compute_urgency

        repo = RequestsRepo()
        touched = 0
        for request in repo.list_open():
            if request.need is None:
                continue
            before = request.urgency
            request.urgency = compute_urgency(
                request.need,
                raw_text=request.raw_text,
                photo_severity=request.photo_severity,
                received_at=request.received_at,
            ).total
            if before != request.urgency:
                repo.save(request)
                touched += 1
        get_bus().publish({"type": "rescan_complete", "rescored": touched})
        return {"rescored": touched}


_service: PipelineService | None = None


def get_pipeline_service() -> PipelineService:
    global _service
    if _service is None:
        _service = PipelineService()
    return _service


def set_pipeline_service(service: PipelineService | None) -> None:
    """Test hook."""
    global _service
    _service = service
