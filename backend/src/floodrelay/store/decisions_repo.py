"""Persistence for decision cards.

A resolved card is the only thing that can unblock a gated action, so this repo
is the authority the human-gate hook consults.
"""

from __future__ import annotations

from ..models.common import utcnow
from ..models.decision import DecisionCard, DecisionOutcome
from .table import Table, dec_pk, get_table


class DecisionsRepo:
    def __init__(self, table: Table | None = None) -> None:
        self.table = table or get_table()

    def save(self, card: DecisionCard) -> DecisionCard:
        self.table.put_model(
            dec_pk(card.id),
            "META",
            card.model_dump(mode="json"),
            extra={"open": "true" if card.is_open else "false", "kind": card.kind},
        )
        return card

    def get(self, decision_id: str) -> DecisionCard | None:
        body = self.table.get_body(dec_pk(decision_id), "META")
        return DecisionCard.model_validate(body) if body else None

    def require(self, decision_id: str) -> DecisionCard:
        found = self.get(decision_id)
        if found is None:
            raise KeyError(f"No such decision: {decision_id}")
        return found

    def list_all(self) -> list[DecisionCard]:
        rows = self.table.backend.scan_prefix("DEC#")
        metas = [r for r in rows if r.get("sk") == "META"]
        out = [DecisionCard.model_validate(b) for b in self.table.bodies(metas)]
        out.sort(key=lambda c: c.created_at)
        return out

    def list_open(self) -> list[DecisionCard]:
        return [c for c in self.list_all() if c.is_open]

    def resolve(
        self, decision_id: str, option_id: str, *, note: str | None = None, by: str = "coordinator"
    ) -> DecisionCard:
        card = self.require(decision_id)
        if not card.is_open:
            raise ValueError(f"Decision {decision_id} is already resolved.")
        valid = {o.id for o in card.options}
        if option_id not in valid:
            raise ValueError(
                f"Option {option_id!r} is not on decision {decision_id}; "
                f"valid options are {sorted(valid)}."
            )
        card.outcome = DecisionOutcome(option_id=option_id, note=note, resolved_by=by)
        card.resolved_at = utcnow()
        card.resolved_by = by
        return self.save(card)
