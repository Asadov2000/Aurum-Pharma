"""Historical physical retail documents; no posting, authorization or live POS.

Version 3 is separate from the prepared-stock v2 documents and event serializers.
The future trusted checkout coordinator must bind these facts to committed stock,
sale and payments. Constructing or hashing a document proves none of those actions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from hashlib import sha256
from typing import Literal, Self
from unicodedata import category
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.domains.inventory.unit_stock_v2 import (
    SaleFormatV2,
    UnitPackagingV2,
    to_base_units_v2,
)
from app.domains.pos.unit_contracts import (
    EXACT_CONTEXT,
    AmountsV2,
    CheckoutPaymentV2,
    CheckoutScopeV2,
    FrozenContract,
    Money,
    Position,
    WholeQuantity,
)
from app.domains.pos.unit_documents import UNIT_LABELS, ReceiptPaymentV2, UnitLabel

PhysicalUnitLabel = UnitLabel | Literal["блист."]


def _printable(value: str) -> str:
    if not value.strip() or any(category(char) == "Cc" for char in value):
        raise ValueError("document text must be printable and nonblank")
    return value


def _positions(positions: tuple[int, ...]) -> None:
    if positions != tuple(range(1, len(positions) + 1)):
        raise ValueError("document positions must be consecutive in original order")


def _balance(amounts: tuple[Decimal, ...], payments: tuple[Decimal, ...], total: Decimal) -> None:
    with localcontext(EXACT_CONTEXT):
        if sum(amounts, Decimal("0.00")) != total:
            raise ValueError("document line amounts do not match total")
        if sum(payments, Decimal("0.00")) != total:
            raise ValueError("document payment amounts do not match total")


def _label(packaging: UnitPackagingV2, sale_format: SaleFormatV2) -> PhysicalUnitLabel:
    if sale_format == "pack":
        return "уп."
    if sale_format == "blister":
        return "блист."
    return UNIT_LABELS[packaging.unit]


class PhysicalRetailLineV3(AmountsV2):
    """Exact original retail facts, including the saved printable product name."""

    inventory_kind: Literal["physical_unit"] = "physical_unit"
    name: str = Field(strict=True, min_length=1, max_length=500)
    packaging: UnitPackagingV2
    sale_format: SaleFormatV2
    quantity: WholeQuantity
    base_units: WholeQuantity

    @field_validator("name")
    @classmethod
    def printable_name(cls, value: str) -> str:
        return _printable(value)

    @field_validator("packaging", mode="before")
    @classmethod
    def historical_packaging(cls, value: object) -> UnitPackagingV2:
        if isinstance(value, UnitPackagingV2):
            value = value.model_dump(mode="python")
        if not isinstance(value, dict) or "version" not in value:
            raise ValueError("physical packaging version must be explicit")
        return UnitPackagingV2.model_validate(value)

    @model_validator(mode="after")
    def retail_arithmetic(self) -> Self:
        expected = to_base_units_v2(
            self.packaging, quantity=self.quantity, sale_format=self.sale_format
        )
        if self.base_units != expected:
            raise ValueError("base units must match the original physical composition")
        with localcontext(EXACT_CONTEXT):
            if self.gross_amount != self.original_retail_unit_price * self.quantity:
                raise ValueError("physical gross must match original price times quantity")
        if self.net_amount <= 0:
            raise ValueError("physical retail net must be positive")
        return self


class PhysicalResultLineV3(PhysicalRetailLineV3):
    """The id is the original persisted physical cart line id, never a new print id."""

    id: UUID
    position: Position
    catalog_id: UUID


class PhysicalCheckoutResultV3(CheckoutScopeV2):
    schema_version: Literal[3]
    document_kind: Literal["physical_checkout"] = "physical_checkout"
    stock_command_id: UUID
    lines: tuple[PhysicalResultLineV3, ...] = Field(min_length=1, max_length=200)
    payments: tuple[CheckoutPaymentV2, ...] = Field(min_length=1, max_length=10)
    total_amount: Money
    currency: Literal["TJS"] = "TJS"

    @field_validator("receipt_number")
    @classmethod
    def printable_receipt_number(cls, value: str) -> str:
        return _printable(value)

    @model_validator(mode="after")
    def document_integrity(self) -> Self:
        _positions(tuple(line.position for line in self.lines))
        if len({line.id for line in self.lines}) != len(self.lines):
            raise ValueError("physical line identifiers must be unique")
        if len({payment.id for payment in self.payments}) != len(self.payments):
            raise ValueError("payment identifiers must be unique")
        _balance(
            tuple(line.net_amount for line in self.lines),
            tuple(payment.amount for payment in self.payments),
            self.total_amount,
        )
        return self


class PhysicalReceiptLineV3(PhysicalRetailLineV3):
    position: Position
    unit_label: PhysicalUnitLabel

    @model_validator(mode="after")
    def display_unit(self) -> Self:
        if self.unit_label != _label(self.packaging, self.sale_format):
            raise ValueError("receipt unit label must match original format and unit")
        return self


class PhysicalReceiptDocumentV3(FrozenContract):
    schema_version: Literal[3]
    document_kind: Literal["physical_receipt"] = "physical_receipt"
    receipt_number: str = Field(strict=True, min_length=1, max_length=100)
    receipt_sequence: Position
    completed_at: AwareDatetime
    is_test: bool = Field(strict=True)
    lines: tuple[PhysicalReceiptLineV3, ...] = Field(min_length=1, max_length=200)
    payments: tuple[ReceiptPaymentV2, ...] = Field(min_length=1, max_length=10)
    total_amount: Money
    currency: Literal["TJS"] = "TJS"

    @field_validator("receipt_number")
    @classmethod
    def printable_receipt_number(cls, value: str) -> str:
        return _printable(value)

    @field_validator("completed_at", mode="before")
    @classmethod
    def timestamp_input(cls, value: object) -> object:
        return CheckoutScopeV2.timestamp_input(value)

    @field_validator("completed_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def receipt_integrity(self) -> Self:
        _positions(tuple(line.position for line in self.lines))
        _balance(
            tuple(line.net_amount for line in self.lines),
            tuple(payment.amount for payment in self.payments),
            self.total_amount,
        )
        return self


def build_physical_receipt(result: PhysicalCheckoutResultV3) -> PhysicalReceiptDocumentV3:
    """Whitelist retail fields; reprinting never resolves today's catalog names."""
    if type(result) is not PhysicalCheckoutResultV3:
        raise ValueError("a physical checkout result is required")
    result = PhysicalCheckoutResultV3.model_validate(result)
    lines = tuple(
        PhysicalReceiptLineV3(
            position=line.position,
            name=line.name,
            inventory_kind=line.inventory_kind,
            packaging=line.packaging,
            sale_format=line.sale_format,
            quantity=line.quantity,
            base_units=line.base_units,
            unit_label=_label(line.packaging, line.sale_format),
            original_retail_unit_price=line.original_retail_unit_price,
            gross_amount=line.gross_amount,
            discount_amount=line.discount_amount,
            net_amount=line.net_amount,
            money_version=line.money_version,
            currency=line.currency,
        )
        for line in result.lines
    )
    return PhysicalReceiptDocumentV3(
        schema_version=3,
        receipt_number=result.receipt_number,
        receipt_sequence=result.receipt_sequence,
        completed_at=result.completed_at,
        is_test=result.is_test,
        lines=lines,
        payments=tuple(
            ReceiptPaymentV2(method=payment.method, amount=payment.amount)
            for payment in result.payments
        ),
        total_amount=result.total_amount,
        currency=result.currency,
    )


PhysicalDocumentV3 = PhysicalCheckoutResultV3 | PhysicalReceiptDocumentV3


def canonical_physical_json(document: PhysicalDocumentV3) -> str:
    """Separate canonical bytes; never accepts or changes the v1/v2 formats."""
    if type(document) not in (PhysicalCheckoutResultV3, PhysicalReceiptDocumentV3):
        raise ValueError("unsupported physical document")
    validated = type(document).model_validate(document)
    return json.dumps(
        validated.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_physical_hash(document: PhysicalDocumentV3) -> str:
    """An integrity checksum, never a signature or proof of committed payment."""
    return sha256(canonical_physical_json(document).encode("utf-8")).hexdigest()
