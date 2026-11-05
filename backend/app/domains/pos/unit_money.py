"""Prepared money allocation; no sale, refund or stock mutation is performed.

The caller must persist the original amount and version, authorize operations,
lock their allocation cursor and prevent overlapping or repeated allocations.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Context, Decimal, InvalidOperation
from typing import Literal

from app.domains.catalog.unit_sales import MAX_BASE_UNITS, MAX_MONEY


@dataclass(frozen=True)
class UnitMoneyAllocation:
    """One immutable range of the original amount, expressed in TJS cents."""

    version: Literal[1]
    currency: Literal["TJS"]
    total_amount: Decimal
    total_units: int
    offset: int
    quantity: int
    amount: Decimal


def _money_cents(amount: Decimal) -> int:
    if (
        not isinstance(amount, Decimal)
        or not amount.is_finite()
        or amount < 0
        or amount > MAX_MONEY
    ):
        raise ValueError("amount must be a finite nonnegative Decimal within the money limit")
    # Explicit context avoids ambient precision/traps. Quantization rejects
    # subcent values by comparison and bounds coefficient conversion to 14 digits.
    context = Context(
        prec=16, rounding=ROUND_DOWN, Emin=-30, Emax=30, clamp=0, traps=[InvalidOperation]
    )
    canonical = amount.quantize(Decimal("0.01"), context=context)
    if canonical != amount:
        raise ValueError("amount must contain whole cents without rounding")
    cents = 0
    for digit in canonical.as_tuple().digits:
        cents = cents * 10 + digit
    return cents


def _from_cents(cents: int) -> Decimal:
    # Tuple construction is exact even under an ambient low-precision context.
    return Decimal((0, tuple(int(digit) for digit in str(cents)), -2))


def allocate_unit_amount(
    total_amount: Decimal,
    *,
    total_units: int,
    offset: int,
    quantity: int,
    version: int = 1,
    currency: Literal["TJS"] = "TJS",
) -> UnitMoneyAllocation:
    """Allocate an adjacent range without changing its original money basis.

    For cost use the original physical pack amount and positions in that pack.
    For refunds use the original homogeneous sale line's net amount after its
    discount and its original count, never current catalog prices. Restoring
    inventory cost requires the original persisted cost slices separately.
    """
    if type(version) is not int or version != 1:
        raise ValueError("unsupported money allocation version")
    if currency != "TJS":
        raise ValueError("unsupported money allocation currency")
    if type(total_units) is not int or not 1 <= total_units <= MAX_BASE_UNITS:
        raise ValueError("total_units must be a positive integer within the stock limit")
    if type(offset) is not int or not 0 <= offset < total_units:
        raise ValueError("offset must identify a position in the original allocation")
    if type(quantity) is not int or not 1 <= quantity <= total_units - offset:
        raise ValueError("quantity must be a positive integer within the remaining range")
    cents = _money_cents(total_amount)
    allocated = cents * (offset + quantity) // total_units - cents * offset // total_units
    return UnitMoneyAllocation(
        version=1,
        currency="TJS",
        total_amount=_from_cents(cents),
        total_units=total_units,
        offset=offset,
        quantity=quantity,
        amount=_from_cents(allocated),
    )
