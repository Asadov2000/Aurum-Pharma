"""Legacy-compatible refund allocation without database or ambient context effects."""

from collections.abc import Mapping
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Context, Decimal, localcontext

from app.core.errors import AurumError, BusinessRuleError


def _cents(amount: Decimal) -> int:
    if not amount.is_finite() or amount < 0:
        raise AurumError("Invalid amount in original payment allocation")
    numerator, denominator = amount.as_integer_ratio()
    cents, remainder = divmod(numerator * 100, denominator)
    if remainder:
        raise AurumError("Invalid amount in original payment allocation")
    return cents


def _money(cents: int) -> Decimal:
    return Decimal(f"{cents // 100}.{cents % 100:02d}")


def allocate_refund_payments(
    *,
    refund_total: Decimal,
    original_amounts: Mapping[str, Decimal],
    already_refunded: Mapping[str, Decimal],
) -> list[tuple[str, Decimal]]:
    """Preserve source order and legacy greedy remainder ties, in bounded time.

    Invalid monetary history fails closed; valid over-refunded methods retain the
    existing zero remaining balance behavior. Unknown historical methods do not
    participate in allocation. Aggregates have no artificial per-row money cap.
    """
    refund = _cents(refund_total)
    original = {method: _cents(amount) for method, amount in original_amounts.items()}
    returned = {method: _cents(amount) for method, amount in already_refunded.items()}
    original = {method: amount for method, amount in original.items() if amount > 0}
    source_total = sum(original.values())
    if source_total <= 0:
        raise BusinessRuleError("Original sale has no valid payment allocation")

    remaining = {
        method: max(0, amount - returned.get(method, 0)) for method, amount in original.items()
    }
    remaining_total = sum(remaining.values())
    if refund > remaining_total:
        raise BusinessRuleError("Refund exceeds the remaining original payment allocation")
    if refund == remaining_total:
        return [(method, _money(amount)) for method, amount in remaining.items() if amount > 0]

    # Preserve the established 28-digit Decimal ranking, including its fractional
    # rounding artifacts. Exact rational ranking would change existing refunds.
    # A private context prevents callers' precision or rounding from changing it.
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        exact = {
            method: _money(refund) * _money(amount) / _money(source_total)
            for method, amount in original.items()
        }
        allocated = {
            method: min(
                remaining[method],
                _cents(exact[method].quantize(Decimal("0.01"), rounding=ROUND_DOWN)),
            )
            for method in original
        }
        remainder_order = sorted(
            original,
            key=lambda method: (
                exact[method] - _money(allocated[method]),
                remaining[method] - allocated[method],
            ),
            reverse=True,
        )
    missing = refund - sum(allocated.values())
    for method in remainder_order:
        added = min(missing, remaining[method] - allocated[method])
        allocated[method] += added
        missing -= added
        if not missing:
            break
    if missing:
        raise AurumError("Unable to allocate refund across original payment methods")

    return [(method, _money(amount)) for method, amount in allocated.items() if amount > 0]
