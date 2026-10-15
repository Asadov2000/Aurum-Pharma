"""Exact internal transfer layout; supplied facts never prove conversion authority.

The future writer must establish accepted immutable incoming cost provenance,
physical identities and current source facts under its transaction locks. This
module neither reads a database nor reconstructs historical packaging or prices.
Opened-pack offsets describe compatibility with the physical cost formula, not
evidence of a historical spending cursor or permission to initialize a ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from typing import Literal
from uuid import UUID

from app.domains.catalog.unit_sales import MAX_MONEY
from app.domains.inventory.unit_legacy_reconciliation import (
    LegacyReconciliationObservation,
    LegacyReconciliationSource,
    preview_legacy_reconciliation,
)
from app.domains.inventory.unit_stock_v2 import _opened_units
from app.domains.pos.unit_money import allocate_unit_amount


@dataclass(frozen=True)
class LegacyTransitionClosedRange:
    pack_ordinal_start: int
    pack_count: int
    base_units: int
    amount: Decimal


@dataclass(frozen=True)
class LegacyTransitionOpenedSlice:
    pack_id: UUID
    pack_ordinal: int
    unit_offset: int
    base_units: int
    amount: Decimal


@dataclass(frozen=True)
class LegacyTransitionPlan:
    """Versioned planning result, distinct from persisted allocation version 1."""

    source: LegacyReconciliationSource
    observation: LegacyReconciliationObservation
    reconciliation_id: UUID
    legacy_quantity_delta: Decimal
    historical_pack_cost: Decimal
    closed_range: LegacyTransitionClosedRange | None
    opened_slices: tuple[LegacyTransitionOpenedSlice, ...]
    total_base_units: int
    total_cost: Decimal
    version: Literal[1] = 1
    allocation_version: Literal[1] = 1
    currency: Literal["TJS"] = "TJS"


def _validated_money(value: Decimal | None) -> Decimal:
    if value is None:
        raise ValueError("historical and carried costs must be explicitly known")
    # Reuse the exact-cent, finite, nonnegative money contract, including zero.
    return allocate_unit_amount(value, total_units=1, offset=0, quantity=1).amount


def _closed_range(
    observation: LegacyReconciliationObservation, pack_cost: Decimal
) -> LegacyTransitionClosedRange | None:
    if observation.closed_packs == 0:
        return None
    with localcontext(Context(prec=60)):
        amount = _validated_money(pack_cost * observation.closed_packs)
    units = observation.closed_packs * observation.packaging.units_per_pack
    amount = allocate_unit_amount(amount, total_units=units, offset=0, quantity=units).amount
    return LegacyTransitionClosedRange(1, observation.closed_packs, units, amount)


def _opened_slices(
    observation: LegacyReconciliationObservation, pack_cost: Decimal
) -> tuple[LegacyTransitionOpenedSlice, ...]:
    parts: list[LegacyTransitionOpenedSlice] = []
    packaging = observation.packaging
    for ordinal, pack in enumerate(
        sorted(observation.opened_packs, key=lambda pack: pack.id.int),
        start=observation.closed_packs + 1,
    ):
        remaining = _opened_units(pack, packaging)
        offset = packaging.units_per_pack - remaining
        allocation = allocate_unit_amount(
            pack_cost,
            total_units=packaging.units_per_pack,
            offset=offset,
            quantity=remaining,
        )
        parts.append(
            LegacyTransitionOpenedSlice(pack.id, ordinal, offset, remaining, allocation.amount)
        )
    return tuple(parts)


def plan_legacy_transition(
    source: LegacyReconciliationSource,
    observation: LegacyReconciliationObservation,
    *,
    tenant_id: UUID,
    branch_id: UUID,
    reconciliation_id: UUID,
    historical_pack_cost: Decimal | None,
    carried_cost: Decimal | None,
) -> LegacyTransitionPlan:
    """Build an exact compatible transfer layout or reject unsupported facts.

    Reconciliation ID and cost inputs are assertions supplied by the caller,
    never proof of persisted identity, authority or accepted-source provenance.
    No rounding, valuation adjustment or inferred price is permitted. The writer
    must independently validate the new ledger's physical and monetary basis.
    """
    if not isinstance(reconciliation_id, UUID):
        raise ValueError("an explicit reconciliation UUID is required")
    comparison = preview_legacy_reconciliation(
        source, observation, tenant_id=tenant_id, branch_id=branch_id
    )
    # The preview reconstructs nested models to reject forged model_copy values.
    source = comparison.source
    observation = comparison.observation
    if (
        not comparison.recorded_quantity_representable
        or comparison.comparison != "matched"
        or comparison.observed_base_units <= 0
    ):
        raise ValueError("transition requires a matching positive exact physical count")
    pack_cost = _validated_money(historical_pack_cost)
    carried = _validated_money(carried_cost)
    with localcontext(Context(prec=60)):
        exact_legacy_cost = _validated_money(source.legacy_quantity * pack_cost)
        legacy_delta = -source.legacy_quantity
    if exact_legacy_cost != carried:
        raise ValueError("carried cost differs from the exact legacy valuation")
    closed = _closed_range(observation, pack_cost)
    opened = _opened_slices(observation, pack_cost)
    with localcontext(Context(prec=60)):
        total = sum((part.amount for part in opened), closed.amount if closed else Decimal("0.00"))
    if total > MAX_MONEY or total != carried:
        raise ValueError("physical cost layout cannot preserve the carried amount")
    return LegacyTransitionPlan(
        source=source,
        observation=observation,
        reconciliation_id=reconciliation_id,
        legacy_quantity_delta=legacy_delta,
        historical_pack_cost=pack_cost,
        closed_range=closed,
        opened_slices=opened,
        total_base_units=comparison.observed_base_units,
        total_cost=carried,
    )
