"""Historical mixed retail documents, without posting or live POS activation.

Version 4 preserves both decimal legacy packs and integer physical formats.
It does not extend the immutable prepared v2 or physical-only v3 domains.
Construction and checksums prove neither authorization nor committed payment.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.domains.pos.physical_documents import (
    PhysicalReceiptLineV3,
    PhysicalResultLineV3,
    _balance,
    _label,
    _positions,
    _printable,
)
from app.domains.pos.unit_contracts import (
    CheckoutPaymentV2,
    CheckoutScopeV2,
    FrozenContract,
    LegacyResultLineV2,
    Money,
    Position,
)
from app.domains.pos.unit_documents import ReceiptLineV2, ReceiptPaymentV2


class LegacyResultLineV4(LegacyResultLineV2):
    """Original SaleItem identity, decimal quantity and historical product name."""

    name: str = Field(strict=True, min_length=1, max_length=500)

    @field_validator("name")
    @classmethod
    def printable_name(cls, value: str) -> str:
        return _printable(value)


class LegacyReceiptLineV4(ReceiptLineV2):
    """Customer display facts keep legacy decimal-pack arithmetic unchanged."""

    inventory_kind: Literal["legacy_pack"] = "legacy_pack"
    sale_format: Literal["pack"] = "pack"
    unit_label: Literal["уп."] = "уп."

    @field_validator("name")
    @classmethod
    def printable_name(cls, value: str) -> str:
        return _printable(value)


MixedResultLineV4 = Annotated[
    LegacyResultLineV4 | PhysicalResultLineV3, Field(discriminator="inventory_kind")
]
MixedReceiptLineV4 = Annotated[
    LegacyReceiptLineV4 | PhysicalReceiptLineV3, Field(discriminator="inventory_kind")
]


def _mixed(kinds: set[str]) -> None:
    if kinds != {"legacy_pack", "physical_unit"}:
        raise ValueError("a mixed document requires both legacy and physical lines")


class MixedCheckoutResultV4(CheckoutScopeV2):
    schema_version: Literal[4]
    document_kind: Literal["mixed_checkout"] = "mixed_checkout"
    # This identifies only the physical ledger command; legacy rows retain batch_id.
    stock_command_id: UUID
    lines: tuple[MixedResultLineV4, ...] = Field(min_length=2, max_length=200)
    payments: tuple[CheckoutPaymentV2, ...] = Field(min_length=1, max_length=10)
    total_amount: Money
    currency: Literal["TJS"] = "TJS"

    @field_validator("receipt_number")
    @classmethod
    def printable_receipt_number(cls, value: str) -> str:
        return _printable(value)

    @model_validator(mode="after")
    def document_integrity(self) -> Self:
        _mixed({line.inventory_kind for line in self.lines})
        _positions(tuple(line.position for line in self.lines))
        if len({line.id for line in self.lines}) != len(self.lines):
            raise ValueError("mixed line identifiers must be unique across both stock kinds")
        if len({payment.id for payment in self.payments}) != len(self.payments):
            raise ValueError("payment identifiers must be unique")
        _balance(
            tuple(line.net_amount for line in self.lines),
            tuple(payment.amount for payment in self.payments),
            self.total_amount,
        )
        return self


class MixedReceiptDocumentV4(FrozenContract):
    schema_version: Literal[4]
    document_kind: Literal["mixed_receipt"] = "mixed_receipt"
    receipt_number: str = Field(strict=True, min_length=1, max_length=100)
    receipt_sequence: Position
    completed_at: AwareDatetime
    is_test: bool = Field(strict=True)
    lines: tuple[MixedReceiptLineV4, ...] = Field(min_length=2, max_length=200)
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
        _mixed({line.inventory_kind for line in self.lines})
        _positions(tuple(line.position for line in self.lines))
        _balance(
            tuple(line.net_amount for line in self.lines),
            tuple(payment.amount for payment in self.payments),
            self.total_amount,
        )
        return self


def build_mixed_receipt(result: MixedCheckoutResultV4) -> MixedReceiptDocumentV4:
    """Whitelist saved retail facts without resolving today's catalog or stock."""
    if type(result) is not MixedCheckoutResultV4:
        raise ValueError("a mixed checkout result is required")
    result = MixedCheckoutResultV4.model_validate(result)
    lines: list[LegacyReceiptLineV4 | PhysicalReceiptLineV3] = []
    for line in result.lines:
        retail = {
            "position": line.position,
            "name": line.name,
            "inventory_kind": line.inventory_kind,
            "quantity": line.quantity,
            "original_retail_unit_price": line.original_retail_unit_price,
            "gross_amount": line.gross_amount,
            "discount_amount": line.discount_amount,
            "net_amount": line.net_amount,
            "money_version": line.money_version,
            "currency": line.currency,
        }
        if isinstance(line, LegacyResultLineV4):
            lines.append(LegacyReceiptLineV4.model_validate(retail))
        else:
            lines.append(
                PhysicalReceiptLineV3.model_validate(
                    {
                        **retail,
                        "packaging": line.packaging,
                        "sale_format": line.sale_format,
                        "base_units": line.base_units,
                        "unit_label": _label(line.packaging, line.sale_format),
                    }
                )
            )
    return MixedReceiptDocumentV4(
        schema_version=4,
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


MixedDocumentV4 = MixedCheckoutResultV4 | MixedReceiptDocumentV4


def canonical_mixed_json(document: MixedDocumentV4) -> str:
    """Stable bytes in a separate domain; old v2/v3 serializers stay unchanged."""
    if type(document) not in (MixedCheckoutResultV4, MixedReceiptDocumentV4):
        raise ValueError("unsupported mixed document")
    validated = type(document).model_validate(document)
    return json.dumps(
        validated.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_mixed_hash(document: MixedDocumentV4) -> str:
    """Integrity checksum, never proof of sale, stock mutation or authorization."""
    return sha256(canonical_mixed_json(document).encode("utf-8")).hexdigest()
