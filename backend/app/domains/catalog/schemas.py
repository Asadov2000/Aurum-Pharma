"""Pydantic schemas for the catalog domain."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

DISPENSING_TYPES = {"prescription", "otc", "special"}
STORAGE_TYPES = {"normal", "cold", "frozen"}
BARCODE_TYPES = {"ean13", "ean8", "gs1_128", "code128", "qr", "other"}


class CatalogItemCreate(BaseModel):
    brand_name: str = Field(min_length=1, max_length=500)
    inn: str | None = None
    manufacturer: str | None = None
    form: str | None = None
    dosage: str | None = None
    pack_size: str | None = None
    atx_code: str | None = None
    dispensing_type: str = "otc"
    storage_type: str = "normal"
    category: str | None = None
    base_price: Decimal | None = None

    @field_validator("dispensing_type")
    @classmethod
    def _check_dispensing(cls, v: str) -> str:
        if v not in DISPENSING_TYPES:
            raise ValueError(f"dispensing_type must be one of {sorted(DISPENSING_TYPES)}")
        return v

    @field_validator("storage_type")
    @classmethod
    def _check_storage(cls, v: str) -> str:
        if v not in STORAGE_TYPES:
            raise ValueError(f"storage_type must be one of {sorted(STORAGE_TYPES)}")
        return v


class CatalogItemUpdate(BaseModel):
    brand_name: str | None = Field(default=None, min_length=1, max_length=500)
    inn: str | None = None
    manufacturer: str | None = None
    form: str | None = None
    dosage: str | None = None
    pack_size: str | None = None
    atx_code: str | None = None
    dispensing_type: str | None = None
    storage_type: str | None = None
    category: str | None = None
    base_price: Decimal | None = None
    is_active: bool | None = None

    @field_validator("dispensing_type")
    @classmethod
    def _check_dispensing(cls, v: str | None) -> str | None:
        if v is not None and v not in DISPENSING_TYPES:
            raise ValueError(f"dispensing_type must be one of {sorted(DISPENSING_TYPES)}")
        return v

    @field_validator("storage_type")
    @classmethod
    def _check_storage(cls, v: str | None) -> str | None:
        if v is not None and v not in STORAGE_TYPES:
            raise ValueError(f"storage_type must be one of {sorted(STORAGE_TYPES)}")
        return v


class BarcodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    code_type: str


class CatalogItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    brand_name: str
    inn: str | None
    manufacturer: str | None
    form: str | None
    dosage: str | None
    pack_size: str | None
    atx_code: str | None
    dispensing_type: str
    storage_type: str
    category: str | None
    base_price: Decimal | None
    currency: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # Additive: available stock at a branch, populated only when the search is
    # called with branch_id (e.g. the POS register's branch). Null otherwise.
    stock_available: Decimal | None = None


class CatalogItemWithBarcodes(CatalogItemRead):
    barcodes: list[BarcodeRead]


class CatalogList(BaseModel):
    items: list[CatalogItemRead]
    total: int
    page: int
    page_size: int


class BarcodeCreate(BaseModel):
    code: str = Field(min_length=1, max_length=200)
    code_type: str = "ean13"

    @field_validator("code_type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        if v not in BARCODE_TYPES:
            raise ValueError(f"code_type must be one of {sorted(BARCODE_TYPES)}")
        return v


# -----------------------------------------------------------------------------
# Import job
# -----------------------------------------------------------------------------


class ImportJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    source_filename: str
    status: str
    duplicate_strategy: str
    total_rows: int | None
    valid_rows: int | None
    error_rows: int | None
    preview_data: list[dict[str, Any]] | None
    errors: list[dict[str, Any]] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    expires_at_for_rollback: datetime | None
    rolled_back_at: datetime | None


class ImportConfirmRequest(BaseModel):
    duplicate_strategy: str = "skip"

    @field_validator("duplicate_strategy")
    @classmethod
    def _check_strategy(cls, v: str) -> str:
        allowed = {"skip", "update", "create_copy"}
        if v not in allowed:
            raise ValueError(f"duplicate_strategy must be one of {sorted(allowed)}")
        return v
