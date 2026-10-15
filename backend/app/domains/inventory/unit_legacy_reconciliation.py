"""Pure comparison of legacy quantities with explicitly observed physical packs.

No database reads, stock writes, reservations or conversion authorization occur.
Scope equality prevents accidental mixing of supplied snapshots; it does not
prove database identity, permissions, historical packaging or a physical count.
An eventual writer must establish those facts and recheck them independently.
"""

from __future__ import annotations

from decimal import Context, Decimal, InvalidOperation, localcontext
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

from app.domains.catalog.unit_sales import MAX_BASE_UNITS
from app.domains.inventory.unit_stock_v2 import OpenedPackV2, UnitPackagingV2, _opened_units

_QUANTITY_STEP = Decimal("0.001")
_MAX_LEGACY_QUANTITY = Decimal("99999999999.999")


def _exact_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal | str | int):
        raise ValueError("quantity must be exact decimal text, an integer or Decimal")
    try:
        result = value if isinstance(value, Decimal) else Decimal(value)
    except InvalidOperation as error:
        raise ValueError("quantity must be a valid decimal") from error
    if not result.is_finite():
        raise ValueError("quantity must be finite")
    try:
        with localcontext(Context(prec=60)):
            canonical = result.quantize(_QUANTITY_STEP)
    except InvalidOperation as error:
        raise ValueError("quantity exceeds exact comparison precision") from error
    if canonical != result:
        raise ValueError("quantity must contain whole thousandths without rounding")
    return canonical


ExactUnits = Annotated[
    Decimal,
    BeforeValidator(_exact_decimal),
    PlainSerializer(lambda value: format(value, ".3f"), return_type=str, when_used="json"),
]


class ReconciliationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class LegacyReconciliationSource(ReconciliationContract):
    """Supplied legacy facts, not a physical lot or evidence of authorization."""

    tenant_id: UUID
    branch_id: UUID
    catalog_id: UUID
    batch_id: UUID
    legacy_quantity: ExactUnits

    @field_validator("legacy_quantity")
    @classmethod
    def validate_quantity(cls, value: Decimal) -> Decimal:
        if not 0 <= value <= _MAX_LEGACY_QUANTITY:
            raise ValueError("legacy quantity must fit nonnegative NUMERIC(14,3)")
        return value


class LegacyReconciliationObservation(ReconciliationContract):
    """Explicit count against a legacy batch; pack UUIDs are observation identities.

    Packaging must be supplied explicitly, never inferred from today's catalog.
    These fields neither identify nor initialize an authoritative physical lot.
    Expiry, blocking and dispense-step eligibility do not remove counted remnants.
    """

    tenant_id: UUID
    branch_id: UUID
    catalog_id: UUID
    batch_id: UUID
    packaging: UnitPackagingV2
    closed_packs: int = Field(strict=True, ge=0, le=MAX_BASE_UNITS)
    opened_packs: tuple[OpenedPackV2, ...]

    @field_validator("packaging", mode="before")
    @classmethod
    def revalidate_packaging(cls, value: object) -> object:
        if isinstance(value, UnitPackagingV2):
            return UnitPackagingV2.model_validate(value.model_dump())
        return value

    @field_validator("opened_packs", mode="before")
    @classmethod
    def revalidate_packs(cls, value: object) -> object:
        if isinstance(value, tuple | list):
            return tuple(
                (
                    OpenedPackV2.model_validate(pack.model_dump())
                    if isinstance(pack, OpenedPackV2)
                    else pack
                )
                for pack in value
            )
        return value

    @model_validator(mode="after")
    def validate_physical_count(self) -> Self:
        if len({pack.id for pack in self.opened_packs}) != len(self.opened_packs):
            raise ValueError("observed opened pack identifiers must be unique")
        # Reuse the same structural rules as UnitStockBatchV2 without inventing a
        # physical lot UUID, receipt date or expiry merely to validate a count.
        total = self.closed_packs * self.packaging.units_per_pack + sum(
            _opened_units(pack, self.packaging) for pack in self.opened_packs
        )
        if total > MAX_BASE_UNITS:
            raise ValueError("observed stock exceeds the base unit limit")
        return self


class LegacyReconciliationPreview(ReconciliationContract):
    """A quantity comparison only; even a match does not approve conversion."""

    source: LegacyReconciliationSource
    observation: LegacyReconciliationObservation
    recorded_base_units: ExactUnits
    observed_base_units: int = Field(strict=True, ge=0, le=MAX_BASE_UNITS)
    discrepancy_base_units: ExactUnits
    fractional_base_units: ExactUnits
    recorded_quantity_representable: bool = Field(strict=True)
    comparison: Literal["matched", "shortage", "surplus"]


def preview_legacy_reconciliation(
    source: LegacyReconciliationSource,
    observation: LegacyReconciliationObservation,
    *,
    tenant_id: UUID,
    branch_id: UUID,
) -> LegacyReconciliationPreview:
    """Compare supplied facts exactly, independently of ambient Decimal context.

    Discrepancy is observed minus recorded. Fractional base units and quantities
    beyond the physical ledger limit are reported, never rounded or clipped.
    Integer representability says nothing about blister integrity, permissions,
    confirmation, dispense eligibility or whether conversion may be committed.
    """
    if not isinstance(tenant_id, UUID) or not isinstance(branch_id, UUID):
        raise ValueError("explicit UUID tenant and branch scope are required")
    if not isinstance(source, LegacyReconciliationSource) or not isinstance(
        observation, LegacyReconciliationObservation
    ):
        raise ValueError("validated reconciliation snapshots are required")
    source = LegacyReconciliationSource.model_validate(source.model_dump())
    observation = LegacyReconciliationObservation.model_validate(observation.model_dump())
    if (source.tenant_id, source.branch_id) != (tenant_id, branch_id) or (
        observation.tenant_id,
        observation.branch_id,
        observation.catalog_id,
        observation.batch_id,
    ) != (tenant_id, branch_id, source.catalog_id, source.batch_id):
        raise ValueError("reconciliation snapshots are outside the requested scope")
    observed = observation.closed_packs * observation.packaging.units_per_pack + sum(
        _opened_units(pack, observation.packaging) for pack in observation.opened_packs
    )
    with localcontext(Context(prec=60)):
        recorded = source.legacy_quantity * observation.packaging.units_per_pack
        fractional = recorded % 1
        discrepancy = Decimal(observed) - recorded
    return LegacyReconciliationPreview(
        source=source,
        observation=observation,
        recorded_base_units=recorded,
        observed_base_units=observed,
        discrepancy_base_units=discrepancy,
        fractional_base_units=fractional,
        recorded_quantity_representable=fractional == 0 and recorded <= MAX_BASE_UNITS,
        comparison="shortage" if discrepancy < 0 else "surplus" if discrepancy > 0 else "matched",
    )
