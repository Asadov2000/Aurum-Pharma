"""Pydantic schemas for the incoming domain."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator

IncomingQuantity = Annotated[
    Decimal,
    Field(gt=0, max_digits=14, decimal_places=3, allow_inf_nan=False),
]
IncomingMoney = Annotated[
    Decimal,
    Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False),
]


class IncomingDocumentCreate(BaseModel):
    operation_id: UUID4
    branch_id: UUID
    supplier_id: UUID
    document_date: date
    document_number: str | None = None
    notes: str | None = None


class IncomingDocumentUpdate(BaseModel):
    branch_id: UUID | None = None
    supplier_id: UUID | None = None
    document_date: date | None = None
    document_number: str | None = None
    notes: str | None = None
    document_file_path: str | None = None

    @field_validator("branch_id", "supplier_id", "document_date")
    @classmethod
    def required_fields_cannot_be_cleared(cls, value: object) -> object:
        if value is None:
            raise ValueError("field cannot be null")
        return value


class IncomingItemBase(BaseModel):
    operation_id: UUID4
    catalog_id: UUID
    batch_number: str | None = None
    manufactured_at: date | None = None
    expires_at: date
    qty: IncomingQuantity
    purchase_price: IncomingMoney
    sale_price: IncomingMoney


class IncomingItemUpdate(BaseModel):
    catalog_id: UUID | None = None
    batch_number: str | None = None
    manufactured_at: date | None = None
    expires_at: date | None = None
    qty: IncomingQuantity | None = None
    purchase_price: IncomingMoney | None = None
    sale_price: IncomingMoney | None = None

    @field_validator("catalog_id", "expires_at", "qty", "purchase_price", "sale_price")
    @classmethod
    def required_fields_cannot_be_cleared(cls, value: object) -> object:
        if value is None:
            raise ValueError("field cannot be null")
        return value


class IncomingItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    catalog_id: UUID
    batch_number: str | None
    manufactured_at: date | None
    expires_at: date
    qty: Decimal
    purchase_price: Decimal
    sale_price: Decimal
    currency: str
    created_batch_id: UUID | None
    catalog_name: str | None = None
    catalog_form: str | None = None
    catalog_dosage: str | None = None
    catalog_pack_size: str | None = None


class IncomingDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    branch_id: UUID
    supplier_id: UUID
    document_number: str | None
    document_date: date
    status: str
    total_amount: Decimal
    currency: str
    notes: str | None
    document_file_path: str | None
    created_at: datetime
    updated_at: datetime
    accepted_at: datetime | None
    branch_name: str | None = None
    supplier_name: str | None = None


class IncomingDocumentSummary(BaseModel):
    all_count: int
    draft_count: int
    accepted_count: int
    rejected_count: int
    accepted_amount: Decimal
    currency: str = "TJS"


class IncomingDocumentList(BaseModel):
    items: list[IncomingDocumentRead]
    total: int
    page: int
    page_size: int
    summary: IncomingDocumentSummary


class IncomingDocumentWithItems(IncomingDocumentRead):
    items: list[IncomingItemRead]
