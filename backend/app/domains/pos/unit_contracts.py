"""Isolated v2 checkout contracts; importing these does not activate unit POS."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from typing import TYPE_CHECKING, Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

from app.domains.catalog.unit_sales import MAX_BASE_UNITS, SaleFormat
from app.domains.inventory.unit_stock import UnitPackaging
from app.domains.pos.unit_money import allocate_unit_amount

if TYPE_CHECKING:
    from app.domains.pos.unit_line_service import UnitSaleLineSnapshot

EXACT_CONTEXT = Context(prec=40, rounding=ROUND_HALF_EVEN)
MAX_LEGACY_QUANTITY = Decimal("99999999999.999")


def _decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise ValueError("decimal must be a Decimal, string or integer, never a float or bool")
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("invalid decimal") from error


def _money(value: object) -> Decimal:
    return allocate_unit_amount(_decimal(value), total_units=1, offset=0, quantity=1).amount


def _legacy_quantity(value: object) -> Decimal:
    quantity = _decimal(value)
    if not quantity.is_finite() or not 0 < quantity <= MAX_LEGACY_QUANTITY:
        raise ValueError("legacy quantity is outside NUMERIC(14,3)")
    with localcontext(EXACT_CONTEXT):
        canonical = quantity.quantize(Decimal("0.001"))
    if quantity != canonical:
        raise ValueError("legacy quantity must contain at most three decimal places")
    return canonical


Money = Annotated[
    Decimal,
    BeforeValidator(_money),
    PlainSerializer(lambda value: format(value, ".2f"), return_type=str, when_used="json"),
]
LegacyQuantity = Annotated[
    Decimal,
    BeforeValidator(_legacy_quantity),
    PlainSerializer(lambda value: format(value, ".3f"), return_type=str, when_used="json"),
]
WholeQuantity = Annotated[int, Field(strict=True, ge=1, le=MAX_BASE_UNITS)]
Position = Annotated[int, Field(strict=True, ge=1)]


class FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    @field_validator("schema_version", "money_version", mode="before", check_fields=False)
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("version must be an integer")
        return value


class LegacyCheckoutLineV2(FrozenContract):
    inventory_kind: Literal["legacy_pack"]
    catalog_id: UUID
    quantity: LegacyQuantity
    discount_amount: Money = Decimal("0.00")


class PreparedCheckoutLineV2(FrozenContract):
    inventory_kind: Literal["prepared_unit"]
    catalog_id: UUID
    sale_format: SaleFormat
    quantity: WholeQuantity
    discount_amount: Money = Decimal("0.00")


CheckoutLineV2 = Annotated[
    LegacyCheckoutLineV2 | PreparedCheckoutLineV2, Field(discriminator="inventory_kind")
]


class CheckoutPaymentV2(FrozenContract):
    id: UUID
    method: Literal["cash", "card", "qr"]
    amount: Money
    currency: Literal["TJS"] = "TJS"

    @model_validator(mode="after")
    def positive_amount(self) -> Self:
        if self.amount <= 0:
            raise ValueError("payment must be positive")
        return self


class CheckoutRequestV2(FrozenContract):
    schema_version: Literal[2]
    operation_id: UUID
    register_id: UUID
    lines: tuple[CheckoutLineV2, ...] = Field(min_length=1, max_length=200)
    payments: tuple[CheckoutPaymentV2, ...] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def unique_payments(self) -> Self:
        if len({payment.id for payment in self.payments}) != len(self.payments):
            raise ValueError("payment identifiers must be unique")
        return self


class CheckoutScopeV2(FrozenContract):
    tenant_id: UUID
    branch_id: UUID
    register_id: UUID
    shift_id: UUID
    cashier_id: UUID
    event_id: UUID
    sale_id: UUID
    operation_id: UUID
    created_at: AwareDatetime
    completed_at: AwareDatetime
    receipt_number: str = Field(strict=True, min_length=1, max_length=100)
    receipt_sequence: Position
    is_test: bool = Field(strict=True)

    @field_validator("created_at", "completed_at", mode="before")
    @classmethod
    def timestamp_input(cls, value: object) -> object:
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        if not isinstance(value, datetime):
            raise ValueError("timestamp must be an aware datetime or ISO string")
        return value

    @field_validator("created_at", "completed_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def timestamp_order(self) -> Self:
        if self.completed_at < self.created_at:
            raise ValueError("completed_at precedes created_at")
        return self


class AmountsV2(FrozenContract):
    original_retail_unit_price: Money
    gross_amount: Money
    discount_amount: Money
    net_amount: Money
    money_version: Literal[1] = 1
    currency: Literal["TJS"] = "TJS"

    @model_validator(mode="after")
    def amounts_balance(self) -> Self:
        with localcontext(EXACT_CONTEXT):
            if self.gross_amount != self.net_amount + self.discount_amount:
                raise ValueError("gross must equal net plus discount")
        if self.original_retail_unit_price <= 0:
            raise ValueError("retail price must be positive")
        return self


class LegacyResultLineV2(AmountsV2):
    inventory_kind: Literal["legacy_pack"]
    id: UUID
    position: Position
    catalog_id: UUID
    batch_id: UUID
    quantity: LegacyQuantity

    @model_validator(mode="after")
    def legacy_arithmetic(self) -> Self:
        with localcontext(EXACT_CONTEXT):
            gross = (self.original_retail_unit_price * self.quantity).quantize(Decimal("0.01"))
        if self.gross_amount != gross:
            raise ValueError("legacy gross must match the original fractional pack quantity")
        return self


class PreparedResultLineV2(AmountsV2):
    inventory_kind: Literal["prepared_unit"]
    id: UUID
    position: Position
    catalog_id: UUID
    prepared_line_id: UUID
    stock_operation_id: UUID
    packaging: UnitPackaging
    sale_format: SaleFormat
    quantity: WholeQuantity
    base_units: WholeQuantity

    @field_validator("packaging", mode="before")
    @classmethod
    def validate_packaging(cls, value: object) -> UnitPackaging:
        if isinstance(value, UnitPackaging):
            value = value.model_dump()
        if isinstance(value, dict) and "version" not in value:
            raise ValueError("packaging version must be explicit")
        return UnitPackaging.model_validate(value)

    @model_validator(mode="after")
    def prepared_arithmetic(self) -> Self:
        if self.net_amount <= 0:
            raise ValueError("prepared sale net must be positive")
        expected = self.quantity
        if self.sale_format == "pack":
            expected *= self.packaging.units_per_pack
        elif self.quantity % self.packaging.dispense_step:
            raise ValueError("quantity must respect the original dispense step")
        if expected != self.base_units or expected > MAX_BASE_UNITS:
            raise ValueError("base units must match original packaging and format")
        with localcontext(EXACT_CONTEXT):
            gross = self.original_retail_unit_price * self.quantity
        if gross != self.gross_amount:
            raise ValueError("prepared gross must equal original price times display quantity")
        return self


ResultLineV2 = Annotated[
    LegacyResultLineV2 | PreparedResultLineV2, Field(discriminator="inventory_kind")
]


class CheckoutResultV2(CheckoutScopeV2):
    schema_version: Literal[2]
    lines: tuple[ResultLineV2, ...] = Field(min_length=1, max_length=200)
    payments: tuple[CheckoutPaymentV2, ...] = Field(min_length=1, max_length=10)
    total_amount: Money
    currency: Literal["TJS"] = "TJS"

    @model_validator(mode="after")
    def document_balance(self) -> Self:
        for values in (
            [line.id for line in self.lines],
            [line.position for line in self.lines],
            [payment.id for payment in self.payments],
            [
                line.prepared_line_id
                for line in self.lines
                if isinstance(line, PreparedResultLineV2)
            ],
            [
                line.stock_operation_id
                for line in self.lines
                if isinstance(line, PreparedResultLineV2)
            ],
        ):
            if len(set(values)) != len(values):
                raise ValueError(
                    "document identifiers, positions and prepared references must be unique"
                )
        with localcontext(EXACT_CONTEXT):
            if sum((line.net_amount for line in self.lines), Decimal(0)) != self.total_amount:
                raise ValueError("line amounts do not match total")
            if sum((payment.amount for payment in self.payments), Decimal(0)) != self.total_amount:
                raise ValueError("payments do not match total")
        return self


def prepared_result_line(
    snapshot: UnitSaleLineSnapshot, *, id: UUID, position: int
) -> PreparedResultLineV2:
    """Copy only original retail facts; procurement amounts never leave the snapshot."""
    return PreparedResultLineV2(
        inventory_kind="prepared_unit",
        id=id,
        position=position,
        catalog_id=snapshot.catalog_id,
        prepared_line_id=snapshot.id,
        stock_operation_id=snapshot.stock_operation_id,
        packaging=UnitPackaging(
            version=snapshot.version,
            unit=snapshot.unit,
            units_per_pack=snapshot.units_per_pack,
            dispense_step=snapshot.dispense_step,
        ),
        sale_format=snapshot.sale_format,
        quantity=snapshot.quantity,
        base_units=snapshot.base_units,
        original_retail_unit_price=snapshot.original_retail_unit_price,
        gross_amount=snapshot.gross_amount,
        discount_amount=snapshot.discount_amount,
        net_amount=snapshot.net_amount,
        money_version=snapshot.money_version,
        currency=snapshot.currency,
    )


def build_checkout_result(
    *,
    scope: CheckoutScopeV2,
    legacy_lines: tuple[LegacyResultLineV2, ...] = (),
    prepared_lines: tuple[tuple[UUID, int, UnitSaleLineSnapshot], ...] = (),
    payments: tuple[CheckoutPaymentV2, ...],
    total_amount: Decimal,
) -> CheckoutResultV2:
    """Build a retail document only from snapshots belonging to its tenant and branch.

    A DTO cannot prove a checkout was committed. Transaction, shift and payment
    authorization remain the future live service's responsibility.
    """
    scope = CheckoutScopeV2.model_validate(scope)
    lines: list[LegacyResultLineV2 | PreparedResultLineV2] = list(legacy_lines)
    for line_id, position, snapshot in prepared_lines:
        if snapshot.tenant_id != scope.tenant_id or snapshot.branch_id != scope.branch_id:
            raise ValueError("prepared snapshot belongs to another tenant or branch")
        lines.append(prepared_result_line(snapshot, id=line_id, position=position))
    return CheckoutResultV2(
        **scope.model_dump(),
        schema_version=2,
        lines=tuple(sorted(lines, key=lambda line: line.position)),
        payments=payments,
        total_amount=total_amount,
        currency="TJS",
    )
