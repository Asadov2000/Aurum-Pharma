"""Immutable internal unit-stock history, without sales or revenue semantics."""

from __future__ import annotations

from datetime import date
from decimal import Context, Decimal, InvalidOperation, localcontext
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    model_validator,
)

from app.domains.catalog.unit_sales import MAX_BASE_UNITS
from app.domains.inventory.unit_stock import UnitPackaging


def _report_amount(value: object) -> Decimal:
    # A stock valuation is an aggregate, not one NUMERIC(14,2) transaction.
    # Converting its SQL numeric text never passes through a JSON float.
    if not isinstance(value, Decimal):
        if isinstance(value, bool) or not isinstance(value, str | int):
            raise ValueError("report amount must be exact decimal text")
        try:
            value = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("report amount must be a valid decimal") from error
    if not value.is_finite() or value < 0:
        raise ValueError("report amount must be finite and nonnegative")
    try:
        with localcontext(Context(prec=60)):
            canonical = value.quantize(Decimal("0.01"))
    except InvalidOperation as error:
        raise ValueError("report amount exceeds the aggregate precision") from error
    if canonical != value:
        raise ValueError("report amount must contain whole cents")
    return canonical


ReportAmount = Annotated[
    Decimal,
    BeforeValidator(_report_amount),
    PlainSerializer(lambda value: format(value, ".2f"), return_type=str, when_used="json"),
]
StockCount = Annotated[int, Field(strict=True, ge=0, le=MAX_BASE_UNITS)]


class ReportContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class UnitStockReportRow(ReportContract):
    lot_id: UUID
    catalog_id: UUID
    source_kind: Literal["received", "prepared"]
    packaging: UnitPackaging
    expires_at: date
    closed_packs: StockCount
    opened_units: StockCount
    base_units: StockCount
    cost_status: Literal["hidden", "unknown", "known"]
    remaining_cost: ReportAmount | None

    @model_validator(mode="after")
    def stock_and_cost(self) -> Self:
        if self.base_units != self.closed_packs * self.packaging.units_per_pack + self.opened_units:
            raise ValueError("historical physical counts do not match base units")
        if (self.cost_status == "known") != (self.remaining_cost is not None):
            raise ValueError("cost visibility and knowledge must match the amount")
        return self


class UnitStockReportSummary(ReportContract):
    lot_count: int = Field(strict=True, ge=0)
    received_lot_count: int = Field(strict=True, ge=0)
    prepared_lot_count: int = Field(strict=True, ge=0)
    cost_status: Literal["hidden", "known", "partial"]
    known_cost_lot_count: int | None = Field(strict=True, ge=0)
    unknown_cost_lot_count: int | None = Field(strict=True, ge=0)
    known_remaining_cost: ReportAmount | None
    received_known_remaining_cost: ReportAmount | None
    prepared_known_remaining_cost: ReportAmount | None

    @model_validator(mode="after")
    def summary_balance(self) -> Self:
        if self.received_lot_count + self.prepared_lot_count != self.lot_count:
            raise ValueError("source counts do not match the number of lots")
        if self.cost_status == "hidden":
            if any(
                value is not None
                for value in (
                    self.known_cost_lot_count,
                    self.unknown_cost_lot_count,
                    self.known_remaining_cost,
                    self.received_known_remaining_cost,
                    self.prepared_known_remaining_cost,
                )
            ):
                raise ValueError("hidden costs must not expose amounts or knowledge counts")
        else:
            if (
                self.known_cost_lot_count is None
                or self.unknown_cost_lot_count is None
                or self.known_remaining_cost is None
                or self.received_known_remaining_cost is None
                or self.prepared_known_remaining_cost is None
                or self.known_cost_lot_count + self.unknown_cost_lot_count != self.lot_count
                or (self.cost_status == "known") != (self.unknown_cost_lot_count == 0)
            ):
                raise ValueError("visible cost coverage is inconsistent")
            with localcontext(Context(prec=60)):
                if self.known_remaining_cost != (
                    self.received_known_remaining_cost + self.prepared_known_remaining_cost
                ):
                    raise ValueError("source cost subtotals do not match known valuation")
        return self


class UnitStockSnapshot(ReportContract):
    summary: UnitStockReportSummary
    items: tuple[UnitStockReportRow, ...]


class UnitStockReport(UnitStockSnapshot):
    tenant_id: UUID
    branch_id: UUID
    on_date: date
    timezone: str
    cutoff_exclusive: AwareDatetime
    page: int = Field(strict=True, ge=1, le=1_000_000)
    page_size: int = Field(strict=True, ge=1, le=100)
    currency: Literal["TJS"] = "TJS"
