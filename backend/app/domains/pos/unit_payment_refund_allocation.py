"""Bind historical goods refunds to their original cash/card/QR payment parts.

This is internal arithmetic, never authorization or proof of terminal movement.
The caller must authenticate the immutable checkout and lock persisted goods and
payment return cursors together. Client-provided cursors must never reach here.
No attempt, payment, receipt, stock movement or database write is created.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal
from uuid import UUID

from app.domains.pos.mixed_documents import LegacyResultLineV4, MixedCheckoutResultV4
from app.domains.pos.mixed_return_allocation import (
    MixedReturnPlan,
    MixedReturnSelection,
    plan_mixed_return,
)
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.physical_return_allocation import (
    PhysicalReturnPlan,
    PhysicalReturnSelection,
    plan_physical_return,
)
from app.domains.pos.refund_payment_allocation import allocate_refund_payments

Checkout = PhysicalCheckoutResultV3 | MixedCheckoutResultV4


@dataclass(frozen=True)
class UnitRefundPaymentPart:
    original_payment_id: UUID
    method: Literal["cash", "card", "qr"]
    amount: Decimal


@dataclass(frozen=True)
class UnitPaymentRefundPlan:
    tenant_id: UUID
    branch_id: UUID
    parent_sale_id: UUID
    parent_operation_id: UUID
    total_amount: Decimal
    goods: PhysicalReturnPlan | MixedReturnPlan
    payments: tuple[UnitRefundPaymentPart, ...]
    currency: Literal["TJS"] = field(default="TJS", init=False)
    state: Literal["calculated_refund"] = field(default="calculated_refund", init=False)


def _cents(amount: Decimal) -> int:
    if type(amount) is not Decimal or not amount.is_finite() or amount < 0:
        raise ValueError("payment cursors must be nonnegative exact money")
    numerator, denominator = amount.as_integer_ratio()
    value, remainder = divmod(numerator * 100, denominator)
    if remainder:
        raise ValueError("payment cursors must be nonnegative exact money")
    return value


def _money(cents: int) -> Decimal:
    return Decimal(f"{cents // 100}.{cents % 100:02d}")


def _legacy_ticks(quantity: Decimal) -> int:
    numerator, denominator = quantity.as_integer_ratio()
    ticks, remainder = divmod(numerator * 1000, denominator)
    if remainder:
        raise ValueError("legacy return cursor must use exact thousandths")
    return ticks


def _prior_goods_cents(
    original: Checkout,
    returned_base_units: Mapping[UUID, int],
    returned_legacy_quantities: Mapping[UUID, Decimal],
) -> int:
    # The goods planner has already validated every cursor, including unselected
    # lines. Its cumulative-floor policy is also used for the previously returned
    # prefix, keeping the final refund equal to the original discounted net.
    total = 0
    for line in original.lines:
        if isinstance(line, LegacyResultLineV4):
            offset = _legacy_ticks(returned_legacy_quantities.get(line.id, Decimal("0")))
            units = _legacy_ticks(line.quantity)
        else:
            offset = returned_base_units.get(line.id, 0)
            units = line.base_units
        total += _cents(line.net_amount) * offset // units
    return total


def _payments(
    original: Checkout,
    goods: PhysicalReturnPlan | MixedReturnPlan,
    returned_payment_amounts: Mapping[UUID, Decimal],
    prior_goods_cents: int,
) -> UnitPaymentRefundPlan:
    sources = {part.id: part for part in original.payments}
    returned: dict[UUID, int] = {}
    for payment_id, amount in returned_payment_amounts.items():
        if type(payment_id) is not UUID or payment_id not in sources:
            raise ValueError("payment cursor must identify an original payment")
        cents = _cents(amount)
        if cents > _cents(sources[payment_id].amount):
            raise ValueError("payment cursor exceeds its original payment")
        returned[payment_id] = cents
    if sum(returned.values()) != prior_goods_cents:
        raise ValueError("goods and payment return history must reconcile")

    original_methods: dict[str, int] = {}
    returned_methods: dict[str, int] = {}
    for source in original.payments:
        method = source.method
        original_methods[method] = original_methods.get(method, 0) + _cents(source.amount)
        returned_methods[method] = returned_methods.get(method, 0) + returned.get(source.id, 0)
    allocated_methods = allocate_refund_payments(
        refund_total=goods.total_amount,
        original_amounts={key: _money(value) for key, value in original_methods.items()},
        already_refunded={key: _money(value) for key, value in returned_methods.items()},
    )
    by_id: dict[UUID, Decimal] = {}
    for allocated_method, amount in allocated_methods:
        matching = tuple(source for source in original.payments if source.method == allocated_method)
        components = allocate_refund_payments(
            refund_total=amount,
            original_amounts={str(source.id): source.amount for source in matching},
            already_refunded={
                str(source.id): _money(returned.get(source.id, 0)) for source in matching
            },
        )
        by_id.update({UUID(key): value for key, value in components})
    parts = tuple(
        UnitRefundPaymentPart(source.id, source.method, by_id[source.id])
        for source in original.payments
        if source.id in by_id
    )
    if sum(_cents(part.amount) for part in parts) != _cents(goods.total_amount):
        raise ValueError("refund payments do not match returned goods")
    return UnitPaymentRefundPlan(
        tenant_id=original.tenant_id,
        branch_id=original.branch_id,
        parent_sale_id=original.sale_id,
        parent_operation_id=original.operation_id,
        total_amount=goods.total_amount,
        goods=goods,
        payments=parts,
    )


def plan_physical_payment_refund(
    checkout: PhysicalCheckoutResultV3,
    selections: tuple[PhysicalReturnSelection, ...],
    *,
    returned_base_units: Mapping[UUID, int],
    returned_payment_amounts: Mapping[UUID, Decimal],
) -> UnitPaymentRefundPlan:
    if type(checkout) is not PhysicalCheckoutResultV3:
        raise ValueError("an original physical checkout v3 is required")
    original = PhysicalCheckoutResultV3.model_validate(checkout.model_dump(mode="python"))
    goods = plan_physical_return(original, selections, returned_base_units=returned_base_units)
    return _payments(
        original,
        goods,
        returned_payment_amounts,
        _prior_goods_cents(original, returned_base_units, {}),
    )


def plan_mixed_payment_refund(
    checkout: MixedCheckoutResultV4,
    selections: tuple[MixedReturnSelection, ...],
    *,
    returned_legacy_quantities: Mapping[UUID, Decimal],
    returned_base_units: Mapping[UUID, int],
    returned_payment_amounts: Mapping[UUID, Decimal],
) -> UnitPaymentRefundPlan:
    if type(checkout) is not MixedCheckoutResultV4:
        raise ValueError("an original mixed checkout v4 is required")
    original = MixedCheckoutResultV4.model_validate(checkout.model_dump(mode="python"))
    goods = plan_mixed_return(
        original,
        selections,
        returned_legacy_quantities=returned_legacy_quantities,
        returned_base_units=returned_base_units,
    )
    return _payments(
        original,
        goods,
        returned_payment_amounts,
        _prior_goods_cents(original, returned_base_units, returned_legacy_quantities),
    )
