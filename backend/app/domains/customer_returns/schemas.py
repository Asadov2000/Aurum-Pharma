"""External schemas for customer-return quarantine."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

CustomerReturnStatus = Literal["pending", "resolved"]
CustomerReturnDispositionType = Literal["disposed", "supplier_claim", "regulatory_transfer"]
CustomerReturnResolutionType = Literal["disposed", "supplier_claim", "regulatory_transfer"]
CustomerReturnReasonCode = Literal["damaged", "quality_issue", "wrong_item", "expired", "other"]


class CustomerReturnRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    branch_id: UUID
    branch_name: str
    return_sale_id: UUID
    return_receipt_number: str | None
    parent_sale_id: UUID
    parent_receipt_number: str | None
    catalog_id: UUID
    catalog_name: str
    catalog_form: str | None
    catalog_dosage: str | None
    batch_id: UUID
    batch_number: str | None
    expires_at: date
    qty: Decimal
    # Historical rows created before controlled reason codes remain readable.
    refund_reason: str | None
    refund_comment: str | None
    received_at: datetime
    received_by: UUID
    status: CustomerReturnStatus
    disposition_type: CustomerReturnDispositionType | None
    disposition_reason: CustomerReturnReasonCode | None
    disposition_comment: str | None
    resolved_at: datetime | None
    resolved_by: UUID | None


class CustomerReturnList(BaseModel):
    items: list[CustomerReturnRead]
    total: int
    pending: int
    resolved: int
    page: int
    page_size: int


class CustomerReturnResolve(BaseModel):
    operation_id: UUID
    disposition_type: CustomerReturnResolutionType
    reason_code: CustomerReturnReasonCode
    comment: str | None = Field(default=None, max_length=2000)

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None
