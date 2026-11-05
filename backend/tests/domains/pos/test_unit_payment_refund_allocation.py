"""Historical goods and tender cursors reconcile without posting a refund."""

from decimal import Decimal, Inexact, Rounded, localcontext
from uuid import UUID

import pytest

from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.mixed_return_allocation import LegacyReturnSelection
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.unit_payment_refund_allocation import (
    UnitPaymentRefundPlan,
    plan_mixed_payment_refund,
    plan_physical_payment_refund,
)
from tests.domains.pos.test_mixed_documents import mixed_payload
from tests.domains.pos.test_physical_documents import line, result_payload

LINE_ID = UUID(int=20)
CASH_ID = UUID(int=22)
CARD_ID = UUID(int=23)
QR_ID = UUID(int=24)


def checkout() -> PhysicalCheckoutResultV3:
    return PhysicalCheckoutResultV3.model_validate(
        result_payload(
            lines=[
                line(
                    id=LINE_ID,
                    position=1,
                    catalog_id=UUID(int=21),
                    sale_format="unit",
                    quantity=6,
                    base_units=6,
                    original_retail_unit_price="0.01",
                    gross_amount="0.06",
                    discount_amount="0.00",
                    net_amount="0.06",
                )
            ],
            payments=[
                {"id": CASH_ID, "method": "cash", "amount": "0.01"},
                {"id": CARD_ID, "method": "card", "amount": "0.01"},
                {"id": QR_ID, "method": "qr", "amount": "0.04"},
            ],
            total_amount="0.06",
        )
    )


def selection(quantity: int) -> tuple[PhysicalReturnSelection, ...]:
    return (PhysicalReturnSelection(LINE_ID, "unit", quantity),)


def payments(plan: UnitPaymentRefundPlan) -> list[tuple[UUID, str, Decimal]]:
    return [(part.original_payment_id, part.method, part.amount) for part in plan.payments]


def test_legacy_rounding_keeps_original_payment_ids_and_scope() -> None:
    source = checkout()
    before = source.model_dump(mode="json")
    plan = plan_physical_payment_refund(
        source, selection(2), returned_base_units={}, returned_payment_amounts={}
    )
    assert payments(plan) == [(CASH_ID, "cash", Decimal("0.01")), (QR_ID, "qr", Decimal("0.01"))]
    assert plan.total_amount == plan.goods.total_amount == Decimal("0.02")
    assert (plan.tenant_id, plan.branch_id) == (source.tenant_id, source.branch_id)
    assert (plan.parent_sale_id, plan.parent_operation_id) == (source.sale_id, source.operation_id)
    assert (plan.currency, plan.state) == ("TJS", "calculated_refund")
    assert source.model_dump(mode="json") == before


def test_repeated_partials_and_final_return_exhaust_each_original_component() -> None:
    source = checkout()
    history: dict[UUID, Decimal] = {}
    offset = 0
    for quantity in (2, 1, 3):
        plan = plan_physical_payment_refund(
            source,
            selection(quantity),
            returned_base_units={LINE_ID: offset},
            returned_payment_amounts=history,
        )
        assert sum((part.amount for part in plan.payments), Decimal("0")) == plan.total_amount
        for part in plan.payments:
            previous = history.get(part.original_payment_id, Decimal("0"))
            history[part.original_payment_id] = previous + part.amount
        offset += quantity
    assert history == {CASH_ID: Decimal("0.01"), CARD_ID: Decimal("0.01"), QR_ID: Decimal("0.04")}
    with pytest.raises(ValueError):
        plan_physical_payment_refund(
            source, selection(1), returned_base_units={LINE_ID: 6}, returned_payment_amounts=history
        )


def test_duplicate_method_components_remain_distinct_and_close_exactly() -> None:
    payload = checkout().model_dump(mode="python")
    payload["payments"] = [
        {"id": CASH_ID, "method": "card", "amount": "0.03"},
        {"id": CARD_ID, "method": "card", "amount": "0.03"},
    ]
    source = PhysicalCheckoutResultV3.model_validate(payload)
    first = plan_physical_payment_refund(
        source, selection(1), returned_base_units={}, returned_payment_amounts={}
    )
    assert payments(first) == [(CASH_ID, "card", Decimal("0.01"))]
    last = plan_physical_payment_refund(
        source,
        selection(5),
        returned_base_units={LINE_ID: 1},
        returned_payment_amounts={CASH_ID: Decimal("0.01")},
    )
    assert payments(last) == [
        (CASH_ID, "card", Decimal("0.02")),
        (CARD_ID, "card", Decimal("0.03")),
    ]


def test_mixed_legacy_and_physical_returns_share_original_tenders() -> None:
    source = MixedCheckoutResultV4.model_validate(
        mixed_payload(
            payments=[
                {"id": CARD_ID, "method": "card", "amount": "10.00"},
                {"id": QR_ID, "method": "qr", "amount": "13.52"},
            ]
        )
    )
    first = plan_mixed_payment_refund(
        source,
        (LegacyReturnSelection(UUID(int=40), Decimal("0.001")), *selection(10)),
        returned_legacy_quantities={},
        returned_base_units={},
        returned_payment_amounts={},
    )
    assert first.total_amount == Decimal("11.77")
    history = {part.original_payment_id: part.amount for part in first.payments}
    last = plan_mixed_payment_refund(
        source,
        selection(10),
        returned_legacy_quantities={UUID(int=40): Decimal("0.001")},
        returned_base_units={LINE_ID: 10},
        returned_payment_amounts=history,
    )
    assert last.total_amount == Decimal("11.75")
    for part in last.payments:
        history[part.original_payment_id] += part.amount
    assert history == {CARD_ID: Decimal("10.00"), QR_ID: Decimal("13.52")}


@pytest.mark.parametrize("goods_offset,payment_amount", [(1, "0.00"), (0, "0.01")])
def test_inconsistent_goods_and_payment_history_is_rejected(
    goods_offset: int, payment_amount: str
) -> None:
    with pytest.raises(ValueError, match="history must reconcile"):
        plan_physical_payment_refund(
            checkout(),
            selection(1),
            returned_base_units={LINE_ID: goods_offset},
            returned_payment_amounts={CASH_ID: Decimal(payment_amount)},
        )


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-0.01", "0.001", "0.02"])
def test_invalid_or_excess_original_payment_cursor_is_rejected(amount: str) -> None:
    with pytest.raises(ValueError, match="payment cursor"):
        plan_physical_payment_refund(
            checkout(),
            selection(1),
            returned_base_units={},
            returned_payment_amounts={CASH_ID: Decimal(amount)},
        )


def test_foreign_payment_cursor_is_rejected_even_when_zero() -> None:
    with pytest.raises(ValueError, match="identify an original payment"):
        plan_physical_payment_refund(
            checkout(),
            selection(1),
            returned_base_units={},
            returned_payment_amounts={UUID(int=999): Decimal("0")},
        )


def test_zero_net_goods_allocate_no_payment_then_final_return_closes_total() -> None:
    payload = checkout().model_dump(mode="python")
    payload["lines"] = [
        line(
            id=LINE_ID,
            position=1,
            catalog_id=UUID(int=21),
            sale_format="unit",
            quantity=6,
            base_units=6,
            original_retail_unit_price="0.01",
            gross_amount="0.06",
            discount_amount="0.05",
            net_amount="0.01",
        )
    ]
    payload["payments"] = [{"id": QR_ID, "method": "qr", "amount": "0.01"}]
    payload["total_amount"] = "0.01"
    source = PhysicalCheckoutResultV3.model_validate(payload)
    zero = plan_physical_payment_refund(
        source, selection(5), returned_base_units={}, returned_payment_amounts={}
    )
    assert zero.total_amount == Decimal("0.00")
    assert zero.payments == ()
    last = plan_physical_payment_refund(
        source, selection(1), returned_base_units={LINE_ID: 5}, returned_payment_amounts={}
    )
    assert payments(last) == [(QR_ID, "qr", Decimal("0.01"))]


def test_unvalidated_source_copy_is_revalidated() -> None:
    source = checkout().model_copy(update={"total_amount": Decimal("0.07")})
    with pytest.raises(ValueError):
        plan_physical_payment_refund(
            source, selection(1), returned_base_units={}, returned_payment_amounts={}
        )


def test_refund_is_independent_of_ambient_precision_and_traps() -> None:
    source = checkout()
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        plan = plan_physical_payment_refund(
            source, selection(2), returned_base_units={}, returned_payment_amounts={}
        )
    assert payments(plan) == [(CASH_ID, "cash", Decimal("0.01")), (QR_ID, "qr", Decimal("0.01"))]
