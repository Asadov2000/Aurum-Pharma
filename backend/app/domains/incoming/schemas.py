"""Pydantic schemas for the incoming domain."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator

from app.domains.incoming.acceptance import AcceptanceMode, StockMode
from app.domains.inventory.unit_stock import UnitPackaging

IncomingQuantity = Annotated[
    Decimal,
    Field(gt=0, max_digits=14, decimal_places=3, allow_inf_nan=False),
]
IncomingMoney = Annotated[
    Decimal,
    Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False),
]


class IncomingDocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    operation_id: UUID4
    branch_id: UUID
    supplier_id: UUID
    document_date: date
    document_number: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)


class IncomingDocumentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    branch_id: UUID | None = None
    supplier_id: UUID | None = None
    document_date: date | None = None
    document_number: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)
    document_file_path: str | None = Field(default=None, max_length=1000)

    @field_validator("branch_id", "supplier_id", "document_date")
    @classmethod
    def required_fields_cannot_be_cleared(cls, value: object) -> object:
        if value is None:
            raise ValueError("field cannot be null")
        return value


class IncomingItemBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    operation_id: UUID4
    catalog_id: UUID
    batch_number: str | None = Field(default=None, max_length=120)
    manufactured_at: date | None = None
    expires_at: date
    qty: IncomingQuantity
    purchase_price: IncomingMoney
    sale_price: IncomingMoney
    # Preparation metadata only; this does not enable unit stock or unit sales.
    prepare_unit_sale: bool = Field(default=False, strict=True)


class IncomingItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    catalog_id: UUID | None = None
    batch_number: str | None = Field(default=None, max_length=120)
    manufactured_at: date | None = None
    expires_at: date | None = None
    qty: IncomingQuantity | None = None
    purchase_price: IncomingMoney | None = None
    sale_price: IncomingMoney | None = None
    prepare_unit_sale: bool | None = Field(default=None, strict=True)

    @field_validator(
        "catalog_id", "expires_at", "qty", "purchase_price", "sale_price", "prepare_unit_sale"
    )
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
    purchase_price: Decimal | None
    sale_price: Decimal
    currency: str
    created_batch_id: UUID | None
    unit_packaging_preparation: UnitPackaging | None = None
    catalog_name: str | None = None
    catalog_form: str | None = None
    catalog_dosage: str | None = None
    catalog_pack_size: str | None = None


class IncomingDraftFields(BaseModel):
    """Bounded strings preserve incomplete input without numeric/date coercion."""

    model_config = ConfigDict(extra="forbid", strict=True)

    catalog_id: str = Field(max_length=36)
    source_name: str = Field(default="", max_length=255)
    batch_number: str = Field(max_length=120)
    manufactured_at: str = Field(max_length=32)
    expires_at: str = Field(max_length=32)
    qty: str = Field(max_length=64)
    purchase_price: str = Field(max_length=64)
    sale_price: str = Field(max_length=64)

    @field_validator("catalog_id")
    @classmethod
    def catalog_identifier(cls, value: str) -> str:
        if value and str(UUID(value)) != value:
            raise ValueError("catalog_id must be empty or a canonical UUID")
        return value

    def as_fields(self) -> dict[str, str]:
        return {
            "catalog_id": self.catalog_id,
            "source_name": self.source_name,
            "batch_number": self.batch_number,
            "manufactured_at": self.manufactured_at,
            "expires_at": self.expires_at,
            "qty": self.qty,
            "purchase_price": self.purchase_price,
            "sale_price": self.sale_price,
        }


class IncomingDraftSave(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(ge=0, le=2_147_483_645)
    fields: IncomingDraftFields


class IncomingDraftCommit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(ge=1, le=2_147_483_645)


class IncomingDraftReadFields(BaseModel):
    catalog_id: str
    source_name: str = ""
    batch_number: str
    manufactured_at: str
    expires_at: str
    qty: str
    purchase_price: str | None
    sale_price: str


class IncomingDraftRead(BaseModel):
    id: UUID
    document_id: UUID
    revision: int
    fields: IncomingDraftReadFields
    catalog_name: str | None
    created_at: datetime
    updated_at: datetime


class IncomingAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stock_mode: AcceptanceMode = "legacy_pack"
    expected_revision: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None


class IncomingDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    branch_id: UUID
    supplier_id: UUID
    document_number: str | None
    document_date: date
    status: str
    total_amount: Decimal | None
    currency: str
    notes: str | None
    document_file_path: str | None
    created_at: datetime
    updated_at: datetime
    accepted_at: datetime | None
    branch_name: str | None = None
    supplier_name: str | None = None
    stock_mode: StockMode | None = None


class IncomingDocumentSummary(BaseModel):
    all_count: int
    draft_count: int
    accepted_count: int
    rejected_count: int
    accepted_amount: Decimal | None
    currency: str = "TJS"


class IncomingDocumentList(BaseModel):
    items: list[IncomingDocumentRead]
    total: int
    page: int
    page_size: int
    summary: IncomingDocumentSummary


class IncomingDocumentWithItems(IncomingDocumentRead):
    items: list[IncomingItemRead]
    physical_receiving_ready: bool = False
    acceptance_revision: str | None = None
