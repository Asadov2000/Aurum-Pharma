"""Mixed quarantine display facts, without financial or cost snapshots."""

from datetime import date
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.domains.customer_returns.reasons import RefundReasonCode
from app.domains.pos.unit_contracts import LegacyQuantity
from app.domains.pos.unit_documents import UnitLabel


class MixedCustomerReturnRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: UUID
    line_id: UUID
    inventory_kind: Literal["legacy_pack", "physical_unit"]
    batch_id: UUID | None
    lot_id: UUID | None
    branch_id: UUID
    branch_name: str
    report_timezone: str
    parent_sale_id: UUID
    parent_receipt_number: str
    return_sale_id: UUID | None
    return_receipt_number: str | None
    catalog_id: UUID
    catalog_name: str = Field(min_length=1, max_length=500)
    legacy_quantity: LegacyQuantity | None
    base_units: int | None = Field(strict=True, gt=0)
    unit_label: UnitLabel
    batch_number: str | None
    expires_at: date
    received_at: AwareDatetime
    refund_reason: RefundReasonCode | None
    refund_comment: str | None
    intake_kind: Literal["cash_refund", "zero_return"]
    status: Literal["pending"] = "pending"

    @model_validator(mode="after")
    def quantity_and_intake_kind(self) -> Self:
        if self.inventory_kind == "legacy_pack":
            if (
                self.batch_id is None
                or self.legacy_quantity is None
                or self.lot_id is not None
                or self.base_units is not None
                or self.unit_label != "уп."
            ):
                raise ValueError("Legacy quarantine requires decimal packs and a batch")
        elif (
            self.lot_id is None
            or self.base_units is None
            or self.batch_id is not None
            or self.legacy_quantity is not None
        ):
            raise ValueError("Physical quarantine requires base units and a physical lot")
        if self.intake_kind == "cash_refund":
            if self.return_sale_id is None or self.return_receipt_number is None:
                raise ValueError("Cash intake requires its immutable return receipt")
        elif self.return_sale_id is not None or self.return_receipt_number is not None:
            raise ValueError("Zero intake must not contain a return sale or receipt")
        return self


class MixedCustomerReturnList(BaseModel):
    items: list[MixedCustomerReturnRead]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=200)
