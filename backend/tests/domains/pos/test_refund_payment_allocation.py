"""Pure compatibility and conservation checks for original-tender refunds."""

from collections.abc import Mapping
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Context, Decimal, Inexact, Rounded, localcontext
from itertools import product

import pytest

from app.core.errors import AurumError, BusinessRuleError
from app.domains.pos.refund_payment_allocation import allocate_refund_payments


def money(cents: int) -> Decimal:
    return Decimal(f"{cents // 100}.{cents % 100:02d}")


def legacy_reference(
    refund_total: Decimal,
    original_amounts: Mapping[str, Decimal],
    already_refunded: Mapping[str, Decimal],
) -> list[tuple[str, Decimal]]:
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        return legacy_reference_in_context(refund_total, original_amounts, already_refunded)


def legacy_reference_in_context(
    refund_total: Decimal,
    original_amounts: Mapping[str, Decimal],
    already_refunded: Mapping[str, Decimal],
) -> list[tuple[str, Decimal]]:
    """Frozen valid-input behavior of POSService before extracting the allocator.

    Keep the old Decimal rounding and per-cent loop: test inputs stay tiny, while
    the production allocator must handle large capped histories without that loop.
    """
    per_method = {method: amount for method, amount in original_amounts.items() if amount > 0}
    source_total = sum(per_method.values(), Decimal("0"))
    quantum = Decimal("0.01")
    methods = list(per_method)
    remaining = {
        method: max(Decimal("0"), per_method[method] - already_refunded.get(method, Decimal("0")))
        for method in methods
    }
    remaining_total = sum(remaining.values(), Decimal("0"))
    if refund_total == remaining_total:
        return [(method, remaining[method]) for method in methods if remaining[method] > 0]
    exact = {method: refund_total * per_method[method] / source_total for method in methods}
    allocated = {
        method: min(remaining[method], exact[method].quantize(quantum, rounding=ROUND_DOWN))
        for method in methods
    }
    missing_cents = int(
        ((refund_total - sum(allocated.values(), Decimal("0"))) / quantum).to_integral_value()
    )
    remainder_order = sorted(
        methods,
        key=lambda method: (
            exact[method] - allocated[method],
            remaining[method] - allocated[method],
        ),
        reverse=True,
    )
    for _index in range(missing_cents):
        method = next(
            candidate
            for candidate in remainder_order
            if allocated[candidate] + quantum <= remaining[candidate]
        )
        allocated[method] += quantum
    return [(method, allocated[method]) for method in methods if allocated[method] > 0]


def test_small_cent_partitions_match_legacy_for_every_valid_partial_history() -> None:
    methods = ("cash", "card", "qr")
    for source in product(range(5), repeat=3):
        if not any(source):
            continue
        original = dict(zip(methods, map(money, source), strict=True))
        for history in product(*(range(amount + 1) for amount in source)):
            returned = dict(zip(methods, map(money, history), strict=True))
            for refund in range(sum(source) - sum(history) + 1):
                expected = legacy_reference(money(refund), original, returned)
                actual = allocate_refund_payments(
                    refund_total=money(refund),
                    original_amounts=original,
                    already_refunded=returned,
                )
                assert actual == expected, (source, history, refund)
                assert sum((amount for _, amount in actual), Decimal("0")) == money(refund)
                assert all(
                    0 < amount <= original[method] - returned[method] for method, amount in actual
                )


@pytest.mark.parametrize("method", ["cash", "card", "qr", "bank_transfer"])
def test_each_existing_payment_method_retains_its_identity(method: str) -> None:
    assert allocate_refund_payments(
        refund_total=Decimal("3.33"),
        original_amounts={method: Decimal("10.00")},
        already_refunded={},
    ) == [(method, Decimal("3.33"))]


def test_exact_ties_follow_source_insertion_order_in_both_directions() -> None:
    original = {"cash": Decimal("0.01"), "card": Decimal("0.01"), "qr": Decimal("0.01")}
    assert allocate_refund_payments(
        refund_total=Decimal("0.01"), original_amounts=original, already_refunded={}
    ) == [("cash", Decimal("0.01"))]
    reversed_original = dict(reversed(list(original.items())))
    assert allocate_refund_payments(
        refund_total=Decimal("0.01"), original_amounts=reversed_original, already_refunded={}
    ) == [("qr", Decimal("0.01"))]
    assert allocate_refund_payments(
        refund_total=Decimal("0.02"), original_amounts=original, already_refunded={}
    ) == [("cash", Decimal("0.01")), ("card", Decimal("0.01"))]


def test_legacy_decimal_rounding_priority_is_preserved_for_one_cent_remainder() -> None:
    original = {"cash": Decimal("0.01"), "card": Decimal("0.01"), "qr": Decimal("0.04")}
    expected = [("cash", Decimal("0.01")), ("qr", Decimal("0.01"))]
    assert legacy_reference(Decimal("0.02"), original, {}) == expected
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        actual = allocate_refund_payments(
            refund_total=Decimal("0.02"), original_amounts=original, already_refunded={}
        )
    assert actual == expected


def test_reversed_non_tied_inputs_preserve_method_amounts_and_source_output_order() -> None:
    original = {"cash": Decimal("0.07"), "card": Decimal("0.02"), "qr": Decimal("0.01")}
    for amounts in (original, dict(reversed(list(original.items())))):
        expected = legacy_reference(Decimal("0.04"), amounts, {})
        result = allocate_refund_payments(
            refund_total=Decimal("0.04"), original_amounts=amounts, already_refunded={}
        )
        assert result == expected
        assert dict(result) == {"cash": Decimal("0.04")}


def test_repeated_partial_refunds_finish_at_exact_original_tender_totals() -> None:
    original = {
        "cash": Decimal("0.03"),
        "card": Decimal("0.04"),
        "qr": Decimal("0.02"),
        "bank_transfer": Decimal("0.01"),
    }
    returned: dict[str, Decimal] = {}
    for cents in (1, 2, 1, 3, 3):
        expected = legacy_reference(money(cents), original, returned)
        result = allocate_refund_payments(
            refund_total=money(cents), original_amounts=original, already_refunded=returned
        )
        assert result == expected
        for method, amount in result:
            returned[method] = returned.get(method, Decimal("0")) + amount
            assert returned[method] <= original[method]
    assert returned == original
    assert (
        allocate_refund_payments(
            refund_total=Decimal("0.00"), original_amounts=original, already_refunded=returned
        )
        == []
    )
    with pytest.raises(BusinessRuleError, match="exceeds the remaining"):
        allocate_refund_payments(
            refund_total=Decimal("0.01"), original_amounts=original, already_refunded=returned
        )


def test_zero_sources_unknown_history_and_over_refunded_sources_keep_legacy_semantics() -> None:
    original = {"cash": Decimal("1.00"), "qr": Decimal("0"), "card": Decimal("2.00")}
    history = {"cash": Decimal("3.00"), "bank_transfer": Decimal("99.00")}
    original_before, history_before = original.copy(), history.copy()
    assert allocate_refund_payments(
        refund_total=Decimal("2.00"), original_amounts=original, already_refunded=history
    ) == [("card", Decimal("2.00"))]
    assert original == original_before and history == history_before
    assert (
        allocate_refund_payments(
            refund_total=Decimal("0"), original_amounts=original, already_refunded={}
        )
        == []
    )
    with pytest.raises(BusinessRuleError, match="exceeds the remaining"):
        allocate_refund_payments(
            refund_total=Decimal("2.01"), original_amounts=original, already_refunded=history
        )


@pytest.mark.parametrize("original", [{}, {"cash": Decimal("0.00")}])
def test_empty_positive_original_allocation_is_rejected(original: dict[str, Decimal]) -> None:
    with pytest.raises(BusinessRuleError, match="no valid payment allocation"):
        allocate_refund_payments(
            refund_total=Decimal("0"), original_amounts=original, already_refunded={}
        )


@pytest.mark.parametrize("bad", ["NaN", "sNaN", "Infinity", "-Infinity", "-0.01", "0.001"])
@pytest.mark.parametrize("field", ["refund", "original", "history", "unknown_history"])
def test_invalid_money_fails_closed_including_unused_historical_methods(
    bad: str, field: str
) -> None:
    refund = Decimal(bad) if field == "refund" else Decimal("0.01")
    original = {"card": Decimal(bad) if field == "original" else Decimal("1.00")}
    history = {"card": Decimal(bad)} if field == "history" else {}
    if field == "unknown_history":
        history["bank_transfer"] = Decimal(bad)
    with pytest.raises(AurumError):
        allocate_refund_payments(
            refund_total=refund, original_amounts=original, already_refunded=history
        )


def test_large_exhausted_source_does_not_require_per_cent_redistribution() -> None:
    # Legacy redistributed four trillion cents one by one. This is a normal
    # exact-result test, without a machine-dependent elapsed-time threshold.
    assert allocate_refund_payments(
        refund_total=Decimal("45000000000.00"),
        original_amounts={"cash": Decimal("800000000000.00"), "card": Decimal("100000000000.00")},
        already_refunded={"cash": Decimal("800000000000.00")},
    ) == [("card", Decimal("45000000000.00"))]


def test_large_skew_uses_fixed_greedy_remainder_priority_not_round_robin() -> None:
    assert allocate_refund_payments(
        refund_total=Decimal("50000000000.00"),
        original_amounts={
            "cash": Decimal("800000000000.00"),
            "card": Decimal("90000000000.00"),
            "qr": Decimal("10000000000.00"),
        },
        already_refunded={"cash": Decimal("800000000000.00")},
    ) == [("card", Decimal("40000000000.00")), ("qr", Decimal("10000000000.00"))]


def test_allocation_ignores_decimal_precision_and_inexact_traps() -> None:
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        result = allocate_refund_payments(
            refund_total=Decimal("1234567890.12"),
            original_amounts={"cash": Decimal("800000000000.00"), "qr": Decimal("100000000000.00")},
            already_refunded={"cash": Decimal("800000000000.00")},
        )
    assert result == [("qr", Decimal("1234567890.12"))]
