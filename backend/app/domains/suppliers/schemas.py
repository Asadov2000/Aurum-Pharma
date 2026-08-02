"""Pydantic schemas for the suppliers domain."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationInfo, field_validator

SupplierReturnReason = Literal[
    "damaged",
    "expired",
    "incorrect_delivery",
    "quality_issue",
    "other",
]

SupplierReturnQuantity = Annotated[
    Decimal,
    Field(gt=0, max_digits=14, decimal_places=3, allow_inf_nan=False),
]


def _normalize_supplier_text(value: str | None, info: ValidationInfo) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if info.field_name != "notes":
        normalized = " ".join(normalized.split())
    return normalized or None


class SupplierCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    legal_name: str | None = Field(default=None, max_length=300)
    inn_or_tin: str | None = Field(default=None, max_length=40)
    contact_person: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=50)
    email: EmailStr | None = None
    address: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator(
        "name",
        "legal_name",
        "inn_or_tin",
        "contact_person",
        "phone",
        "address",
        "notes",
    )
    @classmethod
    def _normalize_text(cls, value: str | None, info: ValidationInfo) -> str | None:
        normalized = _normalize_supplier_text(value, info)
        if info.field_name == "name" and normalized is None:
            raise ValueError("name must not be blank")
        return normalized


class SupplierUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    legal_name: str | None = Field(default=None, max_length=300)
    inn_or_tin: str | None = Field(default=None, max_length=40)
    contact_person: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=50)
    email: EmailStr | None = None
    address: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None

    @field_validator(
        "name",
        "legal_name",
        "inn_or_tin",
        "contact_person",
        "phone",
        "address",
        "notes",
    )
    @classmethod
    def _normalize_text(cls, value: str | None, info: ValidationInfo) -> str | None:
        if info.field_name == "name" and value is None:
            raise ValueError("name cannot be null")
        normalized = _normalize_supplier_text(value, info)
        if info.field_name == "name" and normalized is None:
            raise ValueError("name must not be blank")
        return normalized


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


class SupplierSearchSummary(BaseModel):
    all_count: int
    active_count: int
    inactive_count: int
    with_contact_count: int


class SupplierSearchResponse(BaseModel):
    items: list[SupplierRead]
    total: int
    page: int
    page_size: int
    summary: SupplierSearchSummary


class SupplierOptionRead(BaseModel):
    id: UUID
    name: str
    is_active: bool


class SupplierOptionSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str | None = Field(default=None, max_length=200)
    include_inactive: bool = Field(default=False, strict=True)
    selected_id: UUID | None = None
    limit: int = Field(default=20, ge=1, le=50, strict=True)

    @field_validator("q")
    @classmethod
    def _normalize_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class SupplierOptionList(BaseModel):
    items: list[SupplierOptionRead]


class SupplierReturnCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    operation_id: UUID
    supplier_id: UUID
    batch_id: UUID
    qty: SupplierReturnQuantity
    reason: SupplierReturnReason
    comment: str | None = Field(default=None, max_length=2000)
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
    reason: SupplierReturnReason
    comment: str | None
    created_at: datetime


class SupplierReturnCreated(SupplierReturnRead):
    warning: str | None = None


class SupplierReturnDetails(SupplierReturnRead):
    supplier_name: str
    branch_id: UUID
    branch_name: str
    batch_number: str | None
    catalog_name: str
    catalog_form: str | None
    catalog_dosage: str | None
    catalog_pack_size: str | None
    source_document_number: str | None
    report_timezone: str


class SupplierReturnSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_id: UUID | None = None
    branch_id: UUID | None = None
    reason: SupplierReturnReason | None = None
    date_from: date | None = None
    date_to: date | None = None
    page: int = Field(default=1, ge=1, strict=True)
    page_size: int = Field(default=25, ge=1, le=100, strict=True)


class SupplierReturnSummary(BaseModel):
    total_qty: Decimal
    total_amount: Decimal


class SupplierReturnList(BaseModel):
    items: list[SupplierReturnDetails]
    total: int
    page: int
    page_size: int
    summary: SupplierReturnSummary


class SupplierReturnCandidate(BaseModel):
    batch_id: UUID
    source_document_id: UUID
    document_number: str | None
    document_date: date
    branch_id: UUID
    branch_name: str
    catalog_name: str
    catalog_form: str | None
    catalog_dosage: str | None
    catalog_pack_size: str | None
    batch_number: str | None
    expires_at: date
    qty_remaining: Decimal
    purchase_price: Decimal
    currency: str


class SupplierReturnCandidateSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_id: UUID
    branch_id: UUID | None = None
    q: str | None = Field(default=None, max_length=200)
    page: int = Field(default=1, ge=1, strict=True)
    page_size: int = Field(default=20, ge=1, le=50, strict=True)

    @field_validator("q")
    @classmethod
    def _normalize_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class SupplierReturnCandidateList(BaseModel):
    items: list[SupplierReturnCandidate]
    total: int
    page: int
    page_size: int
