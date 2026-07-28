"""Pydantic schemas for the suppliers domain."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class SupplierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    legal_name: str | None = None
    inn_or_tin: str | None = None
    contact_person: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    address: str | None = None
    notes: str | None = None


class SupplierUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    legal_name: str | None = None
    inn_or_tin: str | None = None
    contact_person: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    address: str | None = None
    notes: str | None = None
    is_active: bool | None = None


class SupplierRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    legal_name: str | None
    inn_or_tin: str | None
    contact_person: str | None
    phone: str | None
    email: str | None
    address: str | None
    notes: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SupplierSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str | None = Field(default=None, max_length=200)
    is_active: bool | None = Field(default=None, strict=True)
    page: int = Field(default=1, ge=1, strict=True)
    page_size: int = Field(default=50, ge=1, le=200, strict=True)

    @field_validator("q")
    @classmethod
    def _normalize_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class SupplierSearchResponse(BaseModel):
    items: list[SupplierRead]
    total: int
    page: int
    page_size: int


class SupplierReturnCreate(BaseModel):
    supplier_id: UUID
    batch_id: UUID
    qty: Decimal = Field(gt=0)
    reason: str = Field(min_length=1, max_length=500)
    comment: str | None = None
    source_document_id: UUID | None = None


class SupplierReturnRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    supplier_id: UUID
    batch_id: UUID
    source_document_id: UUID | None
    qty: Decimal
    amount: Decimal
    currency: str
    reason: str
    comment: str | None
    created_at: datetime


class SupplierReturnCreated(SupplierReturnRead):
    """Includes a warning string if the batch did not belong to this supplier."""

    warning: str | None = None
