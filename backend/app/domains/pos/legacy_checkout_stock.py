"""Pure current legacy FEFO checks shared by app and native cash preparation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from app.core.errors import BusinessRuleError
from app.domains.pos.unit_contracts import EXACT_CONTEXT

if TYPE_CHECKING:
    from app.domains.inventory.models import Batch
    from app.domains.pos.models import SaleItem


class LegacyCheckoutItem(Protocol):
    @property
    def id(self) -> UUID: ...

    @property
    def catalog_id(self) -> UUID: ...

    @property
    def batch_id(self) -> UUID: ...

    @property
    def qty(self) -> Decimal: ...


class LegacyCheckoutBatch(Protocol):
    @property
    def id(self) -> UUID: ...

    @property
    def catalog_id(self) -> UUID: ...

    @property
    def qty_remaining(self) -> Decimal: ...

    @property
    def expires_at(self) -> date: ...

    @property
    def created_at(self) -> datetime: ...

    @property
    def is_blocked(self) -> bool: ...


@dataclass(frozen=True)
class LegacyCheckoutAllocation:
    batch_id: UUID
    catalog_id: UUID
    source_item_ids: tuple[UUID, ...]
    quantity: Decimal


def plan_legacy_checkout_allocations(
    *,
    items: Sequence[LegacyCheckoutItem | SaleItem],
    batches: Sequence[LegacyCheckoutBatch | Batch],
    today: date,
) -> tuple[LegacyCheckoutAllocation, ...]:
    allocations: list[LegacyCheckoutAllocation] = []
    for catalog_id in sorted({item.catalog_id for item in items}, key=str):
        selected: dict[UUID, Decimal] = {}
        sources: dict[UUID, list[UUID]] = {}
        with localcontext(EXACT_CONTEXT):
            for item in items:
                if item.catalog_id == catalog_id:
                    selected[item.batch_id] = selected.get(item.batch_id, Decimal(0)) + item.qty
                    sources.setdefault(item.batch_id, []).append(item.id)
            remaining = sum(selected.values(), Decimal(0))
        candidates = sorted(
            (
                batch
                for batch in batches
                if batch.catalog_id == catalog_id
                and not batch.is_blocked
                and batch.expires_at > today
                and batch.qty_remaining > 0
            ),
            key=lambda batch: (batch.expires_at, batch.created_at, str(batch.id)),
        )
        expected: dict[UUID, Decimal] = {}
        with localcontext(EXACT_CONTEXT):
            for batch in candidates:
                take = min(batch.qty_remaining, remaining)
                if take > 0:
                    expected[batch.id] = take
                    remaining -= take
        if remaining > 0:
            raise BusinessRuleError("Insufficient legacy stock for the entire mixed cart")
        if selected != expected:
            raise BusinessRuleError("Legacy cart stock allocation is stale; rebuild the cart")
        allocations.extend(
            LegacyCheckoutAllocation(
                batch_id=batch_id,
                catalog_id=catalog_id,
                source_item_ids=tuple(sorted(sources[batch_id], key=str)),
                quantity=selected[batch_id],
            )
            for batch_id in sorted(selected, key=str)
        )
    return tuple(allocations)
