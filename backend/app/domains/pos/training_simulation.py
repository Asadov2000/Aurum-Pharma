"""Pure training stock simulation, never a receipt or permission to post.

The caller supplies a current retail quote and explicit stock snapshots. This
module neither reads nor writes a database and does not verify cashier authority
or the saved sale's is_test flag. A future server entrypoint must do both. Virtual
opened-pack identities returned by the existing planner must never be posted.
"""

from datetime import date
from decimal import Decimal, localcontext
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BeforeValidator, Field, PlainSerializer, field_validator

from app.domains.inventory.unit_allocation_v2 import UnitCartLineV2, plan_unit_cart_stock_v2
from app.domains.inventory.unit_stock_v2 import UnitStockBatchV2
from app.domains.pos.unit_cart_quote import (
    MixedCartQuoteV2,
    PhysicalUnitDraftQuoteLineV2,
)
from app.domains.pos.unit_contracts import (
    EXACT_CONTEXT,
    MAX_LEGACY_QUANTITY,
    FrozenContract,
    LegacyResultLineV2,
    Money,
    _decimal,
)

MAX_SNAPSHOT_BATCHES = 10_000


def _stock_quantity(value: object) -> Decimal:
    quantity = _decimal(value)
    if not quantity.is_finite() or not Decimal(0) <= quantity <= MAX_LEGACY_QUANTITY:
        raise ValueError("legacy stock quantity is outside NUMERIC(14,3)")
    with localcontext(EXACT_CONTEXT):
        canonical = quantity.quantize(Decimal("0.001"))
    if quantity != canonical:
        raise ValueError("legacy stock quantity requires at most three decimal places")
    return canonical if canonical else Decimal("0.000")


StockQuantity = Annotated[
    Decimal,
    BeforeValidator(_stock_quantity),
    PlainSerializer(lambda value: format(value, ".3f"), return_type=str, when_used="json"),
]


class TrainingLegacyBatchSnapshot(FrozenContract):
    """Legacy fractional-pack availability, without prices or procurement data."""

    id: UUID
    tenant_id: UUID
    branch_id: UUID
    catalog_id: UUID
    expires_at: date = Field(strict=True)
    created_at: AwareDatetime
    quantity_remaining: StockQuantity
    is_blocked: bool = Field(default=False, strict=True)


class TrainingSaleSimulationV1(FrozenContract):
    """Transient virtual before/after facts; no financial or stock command IDs."""

    schema_version: Literal[1] = 1
    state: Literal["simulated_training_sale"] = "simulated_training_sale"
    is_test: Literal[True] = True
    stock_date: date
    quote: MixedCartQuoteV2
    total_amount: Money
    physical_before: tuple[UnitStockBatchV2, ...]
    physical_after: tuple[UnitStockBatchV2, ...]
    legacy_before: tuple[TrainingLegacyBatchSnapshot, ...]
    legacy_after: tuple[TrainingLegacyBatchSnapshot, ...]

    @field_validator("is_test", mode="before")
    @classmethod
    def strict_training_marker(cls, value: object) -> object:
        if value is not True:
            raise ValueError("simulation must carry the strict training marker")
        return value


def _check_physical_scope(quote: MixedCartQuoteV2, batches: tuple[UnitStockBatchV2, ...]) -> None:
    catalogs = {
        line.catalog_id for line in quote.lines if isinstance(line, PhysicalUnitDraftQuoteLineV2)
    }
    batch_ids: set[UUID] = set()
    opened_ids: set[UUID] = set()
    for batch in batches:
        if (batch.tenant_id, batch.branch_id) != (quote.tenant_id, quote.branch_id):
            raise ValueError("physical snapshot is outside the quote scope")
        if batch.catalog_id not in catalogs:
            raise ValueError("physical snapshot catalog is outside the quote")
        if batch.id in batch_ids:
            raise ValueError("physical batch identifiers must be unique")
        batch_ids.add(batch.id)
        for pack in batch.opened_packs:
            if pack.id in opened_ids:
                raise ValueError("opened pack identifiers must be globally unique")
            opened_ids.add(pack.id)


def _checked_snapshots(
    quote: MixedCartQuoteV2,
    physical: tuple[UnitStockBatchV2, ...],
    legacy: tuple[TrainingLegacyBatchSnapshot, ...],
) -> tuple[tuple[UnitStockBatchV2, ...], tuple[TrainingLegacyBatchSnapshot, ...]]:
    if not isinstance(physical, tuple) or len(physical) > MAX_SNAPSHOT_BATCHES:
        raise ValueError("physical snapshots must be a bounded tuple")
    if not isinstance(legacy, tuple) or len(legacy) > MAX_SNAPSHOT_BATCHES:
        raise ValueError("legacy snapshots must be a bounded tuple")
    if any(not isinstance(batch, UnitStockBatchV2) for batch in physical):
        raise ValueError("physical snapshots require version 2 batch models")
    if any(not isinstance(batch, TrainingLegacyBatchSnapshot) for batch in legacy):
        raise ValueError("legacy snapshots require training batch models")
    physical = tuple(UnitStockBatchV2.model_validate(batch.model_dump()) for batch in physical)
    legacy = tuple(
        TrainingLegacyBatchSnapshot.model_validate(batch.model_dump()) for batch in legacy
    )
    _check_physical_scope(quote, physical)
    legacy_catalogs = {
        line.catalog_id for line in quote.lines if isinstance(line, LegacyResultLineV2)
    }
    legacy_ids: set[UUID] = set()
    for legacy_batch in legacy:
        if (legacy_batch.tenant_id, legacy_batch.branch_id) != (quote.tenant_id, quote.branch_id):
            raise ValueError("legacy snapshot is outside the quote scope")
        if legacy_batch.catalog_id not in legacy_catalogs:
            raise ValueError("legacy snapshot catalog is outside the quote")
        if legacy_batch.id in legacy_ids:
            raise ValueError("legacy batch identifiers must be unique")
        legacy_ids.add(legacy_batch.id)
    return (
        tuple(sorted(physical, key=lambda batch: (batch.expires_at, batch.created_at, batch.id))),
        tuple(sorted(legacy, key=lambda batch: (batch.expires_at, batch.created_at, batch.id))),
    )


def _legacy_residual(
    quote: MixedCartQuoteV2,
    batches: tuple[TrainingLegacyBatchSnapshot, ...],
    today: date,
) -> tuple[TrainingLegacyBatchSnapshot, ...]:
    selected: dict[UUID, dict[UUID, Decimal]] = {}
    with localcontext(EXACT_CONTEXT):
        for line in quote.lines:
            if isinstance(line, LegacyResultLineV2):
                quantities = selected.setdefault(line.catalog_id, {})
                quantities[line.batch_id] = (
                    quantities.get(line.batch_id, Decimal(0)) + line.quantity
                )
        for catalog_id, quantities in selected.items():
            remaining = sum(quantities.values(), Decimal(0))
            expected: dict[UUID, Decimal] = {}
            for batch in batches:
                if batch.catalog_id != catalog_id or batch.is_blocked or batch.expires_at <= today:
                    continue
                take = min(batch.quantity_remaining, remaining)
                if take > 0:
                    expected[batch.id] = take
                    remaining -= take
                if remaining == 0:
                    break
            if remaining > 0:
                raise ValueError("insufficient legacy stock for the whole training cart")
            if quantities != expected:
                raise ValueError("legacy training allocation is stale; rebuild the cart")
        return tuple(
            TrainingLegacyBatchSnapshot.model_validate(
                {
                    **batch.model_dump(),
                    "quantity_remaining": batch.quantity_remaining
                    - selected.get(batch.catalog_id, {}).get(batch.id, Decimal(0)),
                }
            )
            for batch in batches
        )


def simulate_training_sale(
    *,
    quote: MixedCartQuoteV2,
    physical_batches: tuple[UnitStockBatchV2, ...],
    legacy_batches: tuple[TrainingLegacyBatchSnapshot, ...] = (),
    today: date,
) -> TrainingSaleSimulationV1:
    """Apply physical/mixed demand to copies, without reserving either inventory.

    Retail arithmetic comes from the existing strict quote. Legacy batch choices
    must match aggregate FEFO, as in mixed checkout; physical allocation delegates
    to the existing planner. Failure returns no partial result or changed input.
    """
    if type(today) is not date:
        raise ValueError("today must be a calendar date")
    if not isinstance(quote, MixedCartQuoteV2):
        raise ValueError("a version 2 retail quote is required")
    quote = MixedCartQuoteV2.model_validate(quote.model_dump())
    physical_lines = tuple(
        UnitCartLineV2(
            id=line.id,
            catalog_id=line.catalog_id,
            packaging=line.packaging,
            sale_format=line.sale_format,
            quantity=line.quantity,
        )
        for line in quote.lines
        if isinstance(line, PhysicalUnitDraftQuoteLineV2)
    )
    if not physical_lines or any(
        not isinstance(line, PhysicalUnitDraftQuoteLineV2 | LegacyResultLineV2)
        for line in quote.lines
    ):
        raise ValueError("training simulation requires physical or mixed physical positions")
    physical, legacy = _checked_snapshots(quote, physical_batches, legacy_batches)
    legacy_after = _legacy_residual(quote, legacy, today)
    plan = plan_unit_cart_stock_v2(
        lines=physical_lines,
        batches=physical,
        tenant_id=quote.tenant_id,
        branch_id=quote.branch_id,
        today=today,
    )
    return TrainingSaleSimulationV1(
        stock_date=today,
        quote=quote,
        total_amount=quote.total_amount,
        physical_before=physical,
        physical_after=plan.residual_batches,
        legacy_before=legacy,
        legacy_after=legacy_after,
    )
