"""Pure retail documents and refund quotes, separate from v1 and live checkout."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from hashlib import sha256
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, ValidationInfo, field_validator, model_validator

from app.domains.catalog.unit_sales import SaleFormat
from app.domains.inventory.unit_stock import UnitPackaging
from app.domains.pos.unit_contracts import (
    EXACT_CONTEXT,
    AmountsV2,
    CheckoutRequestV2,
    CheckoutResultV2,
    FrozenContract,
    LegacyQuantity,
    Money,
    Position,
    PreparedResultLineV2,
    WholeQuantity,
    _legacy_quantity,
)
from app.domains.pos.unit_money import allocate_unit_amount

UnitLabel = Literal["уп.", "табл.", "капс.", "амп.", "саше", "супп."]
UNIT_LABELS: dict[str, UnitLabel] = {
    "tablet": "табл.",
    "capsule": "капс.",
    "ampoule": "амп.",
    "sachet": "саше",
    "suppository": "супп.",
}


class DisplayReceiptLineV2(AmountsV2):
    """Retail display data with no catalog, stock or procurement identifiers."""

    position: Position
    name: str = Field(strict=True, min_length=1, max_length=500)
    inventory_kind: Literal["legacy_pack", "prepared_unit"]
    sale_format: SaleFormat
    quantity: WholeQuantity | LegacyQuantity
    unit_label: UnitLabel

    @field_validator("quantity", mode="before")
    @classmethod
    def original_quantity_format(cls, value: object, info: ValidationInfo) -> object:
        if info.data.get("inventory_kind") == "legacy_pack":
            return _legacy_quantity(value)
        if type(value) is not int:
            raise ValueError("prepared receipt quantity must be an integer")
        return value

    @field_validator("name")
    @classmethod
    def printable_name(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("receipt name must be printable and nonblank")
        return value

    @model_validator(mode="after")
    def display_format(self) -> Self:
        if self.inventory_kind == "prepared_unit" and type(self.quantity) is not int:
            raise ValueError("prepared receipt quantity must be an integer")
        if self.inventory_kind == "legacy_pack" and (
            self.sale_format != "pack" or not isinstance(self.quantity, Decimal)
        ):
            raise ValueError("legacy receipt retains decimal pack semantics")
        if (self.sale_format == "pack") != (self.unit_label == "уп."):
            raise ValueError("receipt unit label must match display format")
        return self


class ReceiptLineV2(DisplayReceiptLineV2):
    @model_validator(mode="after")
    def sale_arithmetic(self) -> Self:
        with localcontext(EXACT_CONTEXT):
            gross = self.original_retail_unit_price * self.quantity
            if self.inventory_kind == "legacy_pack":
                gross = gross.quantize(Decimal("0.01"))
        if gross != self.gross_amount:
            raise ValueError("receipt gross must match displayed price and quantity")
        return self


class ReceiptPaymentV2(FrozenContract):
    method: Literal["cash", "card", "qr"]
    amount: Money

    @model_validator(mode="after")
    def positive_amount(self) -> Self:
        if self.amount <= 0:
            raise ValueError("receipt payment must be positive")
        return self


class ReceiptDocumentV2(FrozenContract):
    schema_version: Literal[2]
    receipt_number: str = Field(strict=True, min_length=1, max_length=100)
    receipt_sequence: Position
    completed_at: AwareDatetime
    is_test: bool = Field(strict=True)
    lines: tuple[ReceiptLineV2, ...] = Field(min_length=1, max_length=200)
    payments: tuple[ReceiptPaymentV2, ...] = Field(min_length=1, max_length=10)
    total_amount: Money
    currency: Literal["TJS"] = "TJS"

    @field_validator("completed_at", mode="before")
    @classmethod
    def timestamp_input(cls, value: object) -> object:
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        if not isinstance(value, datetime):
            raise ValueError("timestamp must be an aware datetime or ISO string")
        return value

    @field_validator("completed_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def receipt_balance(self) -> Self:
        if len({line.position for line in self.lines}) != len(self.lines):
            raise ValueError("receipt positions must be unique")
        with localcontext(EXACT_CONTEXT):
            if sum((line.net_amount for line in self.lines), Decimal(0)) != self.total_amount:
                raise ValueError("receipt lines do not match total")
            if sum((payment.amount for payment in self.payments), Decimal(0)) != self.total_amount:
                raise ValueError("receipt payments do not match total")
        return self


def build_receipt(result: CheckoutResultV2, *, names: Mapping[UUID, str]) -> ReceiptDocumentV2:
    """Resolve display names explicitly; all money comes from the saved result."""
    result = CheckoutResultV2.model_validate(result)
    lines: list[ReceiptLineV2] = []
    for line in result.lines:
        if line.catalog_id not in names:
            raise ValueError("a resolved display name is required for every receipt line")
        sale_format: SaleFormat = "pack"
        unit_label: UnitLabel = "уп."
        if isinstance(line, PreparedResultLineV2):
            sale_format = line.sale_format
            if sale_format == "unit":
                unit_label = UNIT_LABELS[line.packaging.unit]
        lines.append(
            ReceiptLineV2(
                position=line.position,
                name=names[line.catalog_id],
                inventory_kind=line.inventory_kind,
                sale_format=sale_format,
                quantity=line.quantity,
                unit_label=unit_label,
                original_retail_unit_price=line.original_retail_unit_price,
                gross_amount=line.gross_amount,
                discount_amount=line.discount_amount,
                net_amount=line.net_amount,
                money_version=line.money_version,
                currency=line.currency,
            )
        )
    return ReceiptDocumentV2(
        schema_version=2,
        receipt_number=result.receipt_number,
        receipt_sequence=result.receipt_sequence,
        completed_at=result.completed_at,
        is_test=result.is_test,
        lines=tuple(lines),
        payments=tuple(
            ReceiptPaymentV2(method=payment.method, amount=payment.amount)
            for payment in result.payments
        ),
        total_amount=result.total_amount,
        currency=result.currency,
    )


class RefundQuoteV2(AmountsV2):
    """An allocation preview; it neither advances a cursor nor performs a refund."""

    schema_version: Literal[2]
    kind: Literal["refund_quote"] = "refund_quote"
    original_line: PreparedResultLineV2
    quantity: WholeQuantity
    allocation_offset: int = Field(strict=True, ge=0)
    base_units: WholeQuantity

    @model_validator(mode="after")
    def allocation_basis(self) -> Self:
        original = self.original_line
        if self.allocation_offset + self.quantity > original.quantity:
            raise ValueError("refund range exceeds original display quantity")
        factor = original.packaging.units_per_pack if original.sale_format == "pack" else 1
        if self.base_units != self.quantity * factor:
            raise ValueError("refund base units do not match original packaging")
        if original.sale_format == "unit" and (
            self.quantity % original.packaging.dispense_step
            or self.allocation_offset % original.packaging.dispense_step
        ):
            raise ValueError("refund range must respect the original dispense step")
        if self.original_retail_unit_price != original.original_retail_unit_price:
            raise ValueError("refund must retain original retail price")
        allocation = allocate_unit_amount(
            original.net_amount,
            total_units=original.quantity,
            offset=self.allocation_offset,
            quantity=self.quantity,
            version=original.money_version,
            currency=original.currency,
        )
        if self.net_amount != allocation.amount:
            raise ValueError("refund amount does not match original allocation range")
        with localcontext(EXACT_CONTEXT):
            if self.gross_amount != original.original_retail_unit_price * self.quantity:
                raise ValueError("refund gross must match original price and quantity")
        return self


def build_refund_quote(
    original: PreparedResultLineV2, *, quantity: int, allocation_offset: int
) -> RefundQuoteV2:
    """Allocate original net; derive its discount from exact original price.

    Splitting net and discount independently would give some lines a gross
    different from price times quantity. The complement conserves both totals.
    """
    original = PreparedResultLineV2.model_validate(original)
    net = allocate_unit_amount(
        original.net_amount,
        total_units=original.quantity,
        offset=allocation_offset,
        quantity=quantity,
        version=original.money_version,
        currency=original.currency,
    ).amount
    with localcontext(EXACT_CONTEXT):
        gross = original.original_retail_unit_price * quantity
        discount = gross - net
    return RefundQuoteV2(
        schema_version=2,
        original_line=original,
        quantity=quantity,
        allocation_offset=allocation_offset,
        base_units=quantity
        * (original.packaging.units_per_pack if original.sale_format == "pack" else 1),
        original_retail_unit_price=original.original_retail_unit_price,
        gross_amount=gross,
        discount_amount=discount,
        net_amount=net,
        money_version=original.money_version,
        currency=original.currency,
    )


class RefundReceiptLineV2(ReceiptLineV2):
    """Display-only origin facts; no claim that the quoted refund was committed."""

    schema_version: Literal[2]
    kind: Literal["refund_quote"] = "refund_quote"
    original_position: Position
    original_quantity: WholeQuantity
    allocation_offset: int = Field(strict=True, ge=0)
    original_packaging: UnitPackaging

    @field_validator("original_packaging", mode="before")
    @classmethod
    def validate_packaging(cls, value: object) -> UnitPackaging:
        if isinstance(value, UnitPackaging):
            value = value.model_dump()
        if isinstance(value, dict) and "version" not in value:
            raise ValueError("original packaging version must be explicit")
        return UnitPackaging.model_validate(value)

    @model_validator(mode="after")
    def origin_range(self) -> Self:
        if self.inventory_kind != "prepared_unit":
            raise ValueError("this refund receipt requires a prepared original line")
        if self.allocation_offset + self.quantity > self.original_quantity:
            raise ValueError("refund display range exceeds original quantity")
        if self.sale_format == "unit" and (
            self.quantity % self.original_packaging.dispense_step
            or self.allocation_offset % self.original_packaging.dispense_step
        ):
            raise ValueError("refund display range must respect the original dispense step")
        expected_label = (
            "уп." if self.sale_format == "pack" else UNIT_LABELS[self.original_packaging.unit]
        )
        if self.unit_label != expected_label:
            raise ValueError("refund label does not match original packaging")
        return self


def build_refund_receipt_line(quote: RefundQuoteV2, *, name: str) -> RefundReceiptLineV2:
    quote = RefundQuoteV2.model_validate(quote)
    original = quote.original_line
    return RefundReceiptLineV2(
        schema_version=2,
        position=original.position,
        name=name,
        inventory_kind="prepared_unit",
        sale_format=original.sale_format,
        quantity=quote.quantity,
        unit_label=(
            "уп." if original.sale_format == "pack" else UNIT_LABELS[original.packaging.unit]
        ),
        original_retail_unit_price=quote.original_retail_unit_price,
        gross_amount=quote.gross_amount,
        discount_amount=quote.discount_amount,
        net_amount=quote.net_amount,
        money_version=quote.money_version,
        currency=quote.currency,
        original_position=original.position,
        original_quantity=original.quantity,
        allocation_offset=quote.allocation_offset,
        original_packaging=original.packaging,
    )


CanonicalDocumentV2 = (
    CheckoutRequestV2 | CheckoutResultV2 | ReceiptDocumentV2 | RefundQuoteV2 | RefundReceiptLineV2
)


def canonical_json_v2(document: CanonicalDocumentV2) -> str:
    """Canonical v2 JSON only; never modifies the existing v1 event serializer."""
    supported = (
        CheckoutRequestV2,
        CheckoutResultV2,
        ReceiptDocumentV2,
        RefundQuoteV2,
        RefundReceiptLineV2,
    )
    if type(document) not in supported:
        raise ValueError("unsupported v2 document")
    validated = type(document).model_validate(document)
    return json.dumps(
        validated.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_hash_v2(document: CanonicalDocumentV2) -> str:
    return sha256(canonical_json_v2(document).encode("utf-8")).hexdigest()
