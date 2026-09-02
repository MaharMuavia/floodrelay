"""Append-only audit trail.

There is deliberately no update and no delete. Every autonomous action lands
here with the reasoning and the tool calls that produced it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

from ..models.audit import AuditEvent
from .table import Table, audit_pk, get_table


def digest(value: Any, *, limit: int = 240) -> str:
    """A readable, bounded summary of a tool input or output.

    Short values are kept verbatim so the audit screen is legible; long ones are
    truncated with a sha256 prefix appended, so two large payloads can still be
    told apart.
    """
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, default=str, sort_keys=True)
    if len(text) <= limit:
        return text
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"{text[:limit]}... [sha256:{sha}, {len(text)} chars]"


class AuditRepo:
    def __init__(self, table: Table | None = None) -> None:
        self.table = table or get_table()

    def append(self, event: AuditEvent) -> AuditEvent:
        day = event.ts.date().isoformat()
        sk = f"{event.ts.isoformat()}#{event.id}"
        self.table.put_model(audit_pk(day), sk, event.model_dump(mode="json"))
        return event

    def list_for_day(
        self, day: date | str | None = None, limit: int | None = None
    ) -> list[AuditEvent]:
        key = day.isoformat() if isinstance(day, date) else (day or date.today().isoformat())
        rows = self.table.bodies(self.table.backend.query(audit_pk(key), limit=limit))
        return [AuditEvent.model_validate(r) for r in rows]

    def list_recent(self, limit: int = 200) -> list[AuditEvent]:
        rows = self.table.backend.scan_prefix("AUDIT#")
        events = [AuditEvent.model_validate(b) for b in self.table.bodies(rows)]
        events.sort(key=lambda e: e.ts, reverse=True)
        return events[:limit]

    def list_for_request(self, request_id: str) -> list[AuditEvent]:
        events = [e for e in self.list_recent(limit=10_000) if e.request_id == request_id]
        events.sort(key=lambda e: e.ts)
        return events
