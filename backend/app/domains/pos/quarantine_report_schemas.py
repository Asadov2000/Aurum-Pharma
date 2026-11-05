"""Cost-aware quarantine report contracts; quantities retain their physical basis."""

from datetime import date
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from app.domains.pos.schemas import StockValueSummary, TopProductPhysicalQuantity


class QuarantineReportRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inventory_kind: Literal["legacy", "physical"]
    source_kind: Literal["legacy", "physical", "mixed"]
    intake_operation_id: UUID
    line_id: UUID
    stock_id: UUID
    branch_id: UUID
    branch_name: str
    name: str
    received_at: AwareDatetime
    qty: Decimal
    physical_quantity: TopProductPhysicalQuantity | None
    value: Decimal | None
    cost_status: Literal["known", "unknown", "hidden"]

    @model_validator(mode="after")
    def consistent_quantity_and_value(self) -> Self:
        if (self.inventory_kind == "physical") != (self.physical_quantity is not None):
            raise ValueError("Quarantine quantity basis is inconsistent")
        if self.physical_quantity is not None:
            if self.qty != 0 or self.physical_quantity.quantity <= 0:
                raise ValueError("Physical quarantine quantity is invalid")
        elif self.qty <= 0:
            raise ValueError("Legacy quarantine quantity is invalid")
        if (self.cost_status == "known") != (self.value is not None):
            raise ValueError("Quarantine cost visibility is inconsistent")
        if self.value is not None and self.value < 0:
            raise ValueError("Quarantine value must not be negative")
        return self


class QuarantineReportSnapshot(StockValueSummary):
    rows: list[QuarantineReportRow]
    total: int


class QuarantineReportOverview(QuarantineReportSnapshot):
    on_date: date
    currency: Literal["TJS"] = "TJS"
    page: int
    page_size: int
