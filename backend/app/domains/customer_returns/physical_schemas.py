"""Read-only physical quarantine quantities, without financial or cost snapshots."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.domains.customer_returns.reasons import RefundReasonCode
from app.domains.pos.unit_documents import UnitLabel


class PhysicalCustomerReturnRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: UUID
    line_id: UUID
    lot_id: UUID
    branch_id: UUID
    branch_name: str
    report_timezone: str
    parent_sale_id: UUID
    parent_receipt_number: str
    return_sale_id: UUID | None
    return_receipt_number: str | None
    catalog_id: UUID
    catalog_name: str = Field(min_length=1, max_length=500)
    base_units: int = Field(strict=True, gt=0)
    unit_label: UnitLabel
    batch_number: str | None
    expires_at: date
    received_at: AwareDatetime
    refund_reason: RefundReasonCode | None
    refund_comment: str | None
    intake_kind: Literal["cash_refund", "zero_return"]
    status: Literal["pending"] = "pending"


class PhysicalCustomerReturnList(BaseModel):
    items: list[PhysicalCustomerReturnRead]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=200)
