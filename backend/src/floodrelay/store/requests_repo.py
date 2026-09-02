"""Persistence for help requests."""

from __future__ import annotations

from ..models.request import HelpRequest, RequestStatus
from .table import Table, board_gsi1pk, board_gsi1sk, get_table, req_pk

_OPEN_STATUSES: tuple[RequestStatus, ...] = (
    "new",
    "processing",
    "needs_decision",
    "matched",
)


class RequestsRepo:
    def __init__(self, table: Table | None = None) -> None:
        self.table = table or get_table()

    def save(self, request: HelpRequest) -> HelpRequest:
        """Write META plus the NEED/GEO/MATCH projections named in the layout.

        The projections are what the request-detail screen reads to show how the
        record was built up, node by node, without re-parsing the whole META blob.
        """
        payload = request.model_dump(mode="json")
        pk = req_pk(request.id)
        self.table.put_model(
            pk,
            "META",
            payload,
            gsi1pk=board_gsi1pk(request.status),
            gsi1sk=board_gsi1sk(request.urgency, request.id),
            extra={"status": request.status, "request_id": request.id},
        )
        if request.need is not None:
            self.table.put_model(pk, "NEED", request.need.model_dump(mode="json"))
        if request.location is not None:
            self.table.put_model(pk, "GEO", request.location.model_dump(mode="json"))
        if request.matched_resource_id is not None:
            self.table.put_model(
                pk,
                "MATCH",
                {"resource_id": request.matched_resource_id, "request_id": request.id},
            )
        return request

    def get(self, request_id: str) -> HelpRequest | None:
        body = self.table.get_body(req_pk(request_id), "META")
        return HelpRequest.model_validate(body) if body else None

    def require(self, request_id: str) -> HelpRequest:
        found = self.get(request_id)
        if found is None:
            raise KeyError(f"No such request: {request_id}")
        return found

    def list_by_status(self, status: RequestStatus, limit: int | None = None) -> list[HelpRequest]:
        """Most urgent first -- GSI1 stores an inverted urgency key for exactly this."""
        rows = self.table.bodies(
            self.table.backend.query_gsi1(board_gsi1pk(status), limit=limit)
        )
        return [HelpRequest.model_validate(r) for r in rows]

    def list_all(self, limit: int | None = None) -> list[HelpRequest]:
        rows = self.table.backend.scan_prefix("REQ#", limit=None)
        metas = [r for r in rows if r.get("sk") == "META"]
        out = [HelpRequest.model_validate(b) for b in self.table.bodies(metas)]
        out.sort(key=lambda r: (-(r.urgency or 0.0), r.id))
        return out[:limit] if limit else out

    def list_open(self) -> list[HelpRequest]:
        return [r for r in self.list_all() if r.status in _OPEN_STATUSES]

    def delete(self, request_id: str) -> None:
        pk = req_pk(request_id)
        for sk in ("META", "NEED", "GEO", "MATCH"):
            self.table.backend.delete(pk, sk)
