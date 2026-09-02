"""Persistence for the relief resource roster."""

from __future__ import annotations

from ..models.resource import Resource, ResourceKind
from .table import Table, get_table, res_pk


class ResourcesRepo:
    def __init__(self, table: Table | None = None) -> None:
        self.table = table or get_table()

    def save(self, resource: Resource) -> Resource:
        self.table.put_model(
            res_pk(resource.id),
            "META",
            resource.model_dump(mode="json"),
            extra={"status": resource.status, "kind": resource.kind},
        )
        return resource

    def get(self, resource_id: str) -> Resource | None:
        body = self.table.get_body(res_pk(resource_id), "META")
        return Resource.model_validate(body) if body else None

    def require(self, resource_id: str) -> Resource:
        found = self.get(resource_id)
        if found is None:
            raise KeyError(f"No such resource: {resource_id}")
        return found

    def list_all(self) -> list[Resource]:
        rows = self.table.backend.scan_prefix("RES#")
        metas = [r for r in rows if r.get("sk") == "META"]
        out = [Resource.model_validate(b) for b in self.table.bodies(metas)]
        out.sort(key=lambda r: r.id)
        return out

    def search(
        self,
        *,
        kind: ResourceKind | None = None,
        available_only: bool = True,
    ) -> list[Resource]:
        out = self.list_all()
        if kind is not None:
            out = [r for r in out if r.kind == kind]
        if available_only:
            out = [r for r in out if r.status == "available"]
        return out

    def assign(self, resource_id: str, request_id: str) -> Resource:
        """Mark a resource assigned.

        This is state change, not dispatch. The human gate governs whether the
        agent is allowed to reach this function at all -- see agent/hooks.
        """
        resource = self.require(resource_id)
        resource.status = "assigned"
        resource.current_assignment = request_id
        return self.save(resource)

    def release(self, resource_id: str) -> Resource:
        resource = self.require(resource_id)
        resource.status = "available"
        resource.current_assignment = None
        return self.save(resource)
