"""Strict saved training documents; simulated allocations are never payments."""

from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import UUID4, AwareDatetime, Field, field_validator, model_validator

from app.domains.pos.mixed_documents import LegacyReceiptLineV4, MixedReceiptLineV4
from app.domains.pos.physical_documents import PhysicalReceiptLineV3, _label, _positions, _printable
from app.domains.pos.training_simulation import TrainingSaleSimulationV1
from app.domains.pos.unit_cart_quote import MixedCartQuoteV2, PhysicalUnitDraftQuoteLineV2
from app.domains.pos.unit_contracts import (
    EXACT_CONTEXT,
    FrozenContract,
    LegacyResultLineV2,
    Money,
    Position,
)
from app.domains.pos.unit_documents import ReceiptPaymentV2
from app.domains.sync.integrity import canonical_json_hash


def _change(parts: tuple[ReceiptPaymentV2, ...], total: Decimal, tendered: Decimal) -> Decimal:
    if len({part.method for part in parts}) != len(parts):
        raise ValueError("simulated payment methods must be unique")
    with localcontext(EXACT_CONTEXT):
        if total <= 0 or sum((part.amount for part in parts), Decimal(0)) != total:
            raise ValueError("simulated allocations must exactly match the positive total")
        cash = next((part.amount for part in parts if part.method == "cash"), Decimal(0))
        if (not cash and tendered != 0) or tendered < cash:
            raise ValueError("simulated cash tender must cover cash and be zero without cash")
        return tendered - cash


class TrainingCheckoutRequest(FrozenContract):
    schema_version: Literal[1] = 1
    operation_id: UUID4
    sale_id: UUID
    expected_revision_hash: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    expected_total_amount: Money
    simulated_payments: tuple[ReceiptPaymentV2, ...] = Field(min_length=1, max_length=3)
    cash_tendered: Money = Decimal("0.00")
    currency: Literal["TJS"] = "TJS"

    @model_validator(mode="after")
    def balanced_simulated_allocation(self) -> Self:
        _change(self.simulated_payments, self.expected_total_amount, self.cash_tendered)
        return self


def training_checkout_request_hash(request: TrainingCheckoutRequest) -> str:
    request = TrainingCheckoutRequest.model_validate(request.model_dump())
    return canonical_json_hash(request.model_dump(mode="json"))


class _TrainingScope(FrozenContract):
    schema_version: Literal[1] = 1
    is_test: Literal[True] = True
    operation_id: UUID4
    sale_id: UUID
    tenant_id: UUID
    branch_id: UUID
    register_id: UUID
    shift_id: UUID
    cashier_id: UUID
    receipt_number: str = Field(strict=True, min_length=1, max_length=100)
    receipt_sequence: Position
    completed_at: AwareDatetime
    total_amount: Money
    currency: Literal["TJS"] = "TJS"

    @field_validator("is_test", mode="before")
    @classmethod
    def strict_test(cls, value: object) -> object:
        if value is not True:
            raise ValueError("training documents require the strict test marker")
        return value

    @field_validator("receipt_number")
    @classmethod
    def printable_number(cls, value: str) -> str:
        value = _printable(value)
        if not value.startswith("TEST-"):
            raise ValueError("training receipt number requires its explicit TEST marker")
        return value

    @field_validator("completed_at")
    @classmethod
    def utc_completion(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class TrainingReceiptDocumentV1(_TrainingScope):
    document_kind: Literal["training_sale_receipt"] = "training_sale_receipt"
    lines: tuple[MixedReceiptLineV4, ...] = Field(min_length=1, max_length=200)
    simulated_payments: tuple[ReceiptPaymentV2, ...] = Field(min_length=1, max_length=3)
    cash_tendered: Money
    change_amount: Money

    @model_validator(mode="after")
    def receipt_balance(self) -> Self:
        _positions(tuple(line.position for line in self.lines))
        if not any(isinstance(line, PhysicalReceiptLineV3) for line in self.lines):
            raise ValueError("a training unit receipt requires physical positions")
        with localcontext(EXACT_CONTEXT):
            if sum((line.net_amount for line in self.lines), Decimal(0)) != self.total_amount:
                raise ValueError("training receipt lines must balance the saved total")
        if (
            _change(self.simulated_payments, self.total_amount, self.cash_tendered)
            != self.change_amount
        ):
            raise ValueError("training change differs from simulated cash")
        return self


def training_receipt_lines_from_quote(
    quote: MixedCartQuoteV2, names: dict[UUID, str]
) -> tuple[MixedReceiptLineV4, ...]:
    lines: list[MixedReceiptLineV4] = []
    for line in quote.lines:
        name = names.get(line.catalog_id)
        if name is None:
            raise ValueError("training receipt requires every historical product name")
        facts = line.model_dump(exclude={"id", "catalog_id", "batch_id"})
        if isinstance(line, LegacyResultLineV2):
            lines.append(LegacyReceiptLineV4.model_validate({**facts, "name": name}))
        elif isinstance(line, PhysicalUnitDraftQuoteLineV2):
            lines.append(
                PhysicalReceiptLineV3.model_validate(
                    {**facts, "name": name, "unit_label": _label(line.packaging, line.sale_format)}
                )
            )
        else:
            raise ValueError("prepared unit positions cannot become a training physical receipt")
    return tuple(lines)


def _headers(document: _TrainingScope) -> dict[str, object]:
    return document.model_dump(
        include={
            "schema_version",
            "is_test",
            "operation_id",
            "sale_id",
            "tenant_id",
            "branch_id",
            "register_id",
            "shift_id",
            "cashier_id",
            "receipt_number",
            "receipt_sequence",
            "completed_at",
            "total_amount",
            "currency",
        }
    )


class TrainingCheckoutResultV1(_TrainingScope):
    document_kind: Literal["training_checkout"] = "training_checkout"
    request: TrainingCheckoutRequest
    receipt: TrainingReceiptDocumentV1
    simulation: TrainingSaleSimulationV1

    @model_validator(mode="after")
    def historical_binding(self) -> Self:
        quote = self.simulation.quote
        if _headers(self) != _headers(self.receipt):
            raise ValueError("training result and receipt headers differ")
        if (
            self.operation_id != self.request.operation_id
            or self.sale_id != self.request.sale_id
            or self.sale_id != quote.sale_id
            or self.tenant_id != quote.tenant_id
            or self.branch_id != quote.branch_id
            or self.register_id != quote.register_id
            or self.shift_id != quote.shift_id
            or self.cashier_id != quote.cashier_id
            or self.total_amount != quote.total_amount
            or self.total_amount != self.simulation.total_amount
            or self.total_amount != self.request.expected_total_amount
            or self.request.expected_revision_hash != quote.revision_hash
            or self.receipt.simulated_payments != self.request.simulated_payments
            or self.receipt.cash_tendered != self.request.cash_tendered
        ):
            raise ValueError("training result differs from its saved request or quote")
        if len(quote.lines) != len(self.receipt.lines):
            raise ValueError("training receipt does not cover the entire quote")
        names = {
            line.catalog_id: printed.name
            for line, printed in zip(quote.lines, self.receipt.lines, strict=True)
        }
        if training_receipt_lines_from_quote(quote, names) != self.receipt.lines:
            raise ValueError("training receipt differs from historical retail facts")
        return self


class TrainingCheckoutOperationNotFound(FrozenContract):
    state: Literal["not_found"] = "not_found"
    operation_id: UUID4
    sale_id: UUID


class TrainingCheckoutOperationCompleted(FrozenContract):
    state: Literal["completed"] = "completed"
    operation_id: UUID4
    sale_id: UUID
    result: TrainingCheckoutResultV1

    @model_validator(mode="after")
    def operation_binding(self) -> Self:
        if (self.operation_id, self.sale_id) != (self.result.operation_id, self.result.sale_id):
            raise ValueError("training recovery operation differs from its result")
        return self


TrainingCheckoutOperationRead = Annotated[
    TrainingCheckoutOperationNotFound | TrainingCheckoutOperationCompleted,
    Field(discriminator="state"),
]
