"""Pydantic schemas for the incoming domain."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class IncomingDocumentCreate(BaseModel):
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


class IncomingItemBase(BaseModel):
    catalog_id: UUID
    batch_number: str | None = None
    manufactured_at: date | None = None
    expires_at: date
    qty: Decimal = Field(gt=0)
    purchase_price: Decimal = Field(ge=0)
    sale_price: Decimal = Field(ge=0)


class IncomingItemUpdate(BaseModel):
    catalog_id: UUID | None = None
    batch_number: str | None = None
    manufactured_at: date | None = None
    expires_at: date | None = None
    qty: Decimal | None = Field(default=None, gt=0)
    purchase_price: Decimal | None = Field(default=None, ge=0)
    sale_price: Decimal | None = Field(default=None, ge=0)


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


class IncomingDocumentWithItems(IncomingDocumentRead):
    items: list[IncomingItemRead]
