"""Whole-cart previews conserve physical stock without writes or reservations."""

from dataclasses import FrozenInstanceError
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.domains.inventory.unit_stock import UnitStockBatch, UnitStockShortage
from app.domains.pos.unit_cart_quote import UnitDraftQuoteLineV2
from app.domains.pos.unit_cart_stock import UnitCartStockLine, plan_unit_cart_stock
from tests.domains.inventory.test_unit_stock import (
    BRANCH,
    CATALOG,
    TENANT,
    TODAY,
    batch,
    opened,
    packaging,
)


def line(number: int, quantity: int, *, whole: bool = False) -> UnitDraftQuoteLineV2:
    amount = Decimal(quantity).quantize(Decimal("0.01"))
    return UnitDraftQuoteLineV2(
        id=UUID(int=1000 + number),
        position=number,
        catalog_id=CATALOG,
        packaging=packaging(10),
        sale_format="pack" if whole else "unit",
        quantity=quantity,
        base_units=quantity * 10 if whole else quantity,
        original_retail_unit_price=Decimal("1.00"),
        gross_amount=amount,
        discount_amount=Decimal("0.00"),
        net_amount=amount,
    )


def plan(
    lines: tuple[UnitDraftQuoteLineV2, ...], batches: tuple[UnitStockBatch, ...]
) -> tuple[UnitCartStockLine, ...]:
    return plan_unit_cart_stock(
        lines=lines, batches=batches, tenant_id=TENANT, branch_id=BRANCH, today=TODAY
    )


def test_repeated_lines_cannot_overcommit_one_pack() -> None:
    stock = batch(config=packaging(10))
    with pytest.raises(UnitStockShortage) as error:
        plan((line(1, 6), line(2, 5)), (stock,))
    assert error.value.available_base_units == 4
    assert stock.closed_packs == 1 and stock.opened_packs == ()


def test_new_opened_remainder_is_reused_and_inputs_stay_immutable() -> None:
    stock = batch(config=packaging(10))
    lines = (line(1, 3), line(2, 4), line(3, 3))
    result = plan(lines, (stock,))
    first, second, third = (row.plan.picks[0] for row in result)
    assert first.new_packs_opened == 1 and first.new_opened_remainder_units == 7
    assert second.new_packs_opened == third.new_packs_opened == 0
    assert second.opened_packs[0].remaining_units == 3
    assert third.opened_packs[0].remaining_units == 0
    assert second.opened_packs[0].pack_id == third.opened_packs[0].pack_id
    assert second.opened_packs[0].pack_id.version == 5
    assert all(row.plan.lock_opened_pack_ids == () for row in result)
    assert sum(row.plan.base_units for row in result) == 10
    assert result == plan(lines, (stock,))
    assert stock.closed_packs == 1 and stock.opened_packs == ()
    with pytest.raises(FrozenInstanceError):
        result[0].__setattr__("item_id", UUID(int=9))


def test_whole_pack_is_reserved_before_units_without_reassembling_opened_stock() -> None:
    early = batch(1, config=packaging(10), expiry=5)
    later = batch(2, closed=0, config=packaging(10), packs=(opened(12, 4),), expiry=10)
    lines = (line(1, 4), line(2, 1, whole=True))
    result = plan(lines, (early, later))
    assert tuple(row.item_id for row in result) == (lines[1].id, lines[0].id)
    assert result[0].plan.picks[0].batch_id == early.id
    assert result[0].plan.picks[0].closed_packs_taken == 1
    assert result[0].plan.picks[0].opened_packs == ()
    assert result[1].plan.picks[0].batch_id == later.id
    with pytest.raises(UnitStockShortage):
        plan((line(1, 1, whole=True),), (later,))


def test_repeated_pack_lines_cannot_reuse_closed_pack() -> None:
    with pytest.raises(UnitStockShortage) as error:
        plan((line(1, 1, whole=True), line(2, 1, whole=True)), (batch(config=packaging(10)),))
    assert error.value.available_base_units == 0


@pytest.mark.parametrize("exclusion", ["expiry", "blocked", "packaging"])
def test_ineligible_stock_is_excluded_for_the_entire_cart(exclusion: str) -> None:
    stock = batch(config=packaging(10))
    changes: dict[str, object] = {
        "expiry": {"expires_at": TODAY},
        "blocked": {"is_blocked": True},
        "packaging": {"packaging": packaging(20)},
    }[exclusion]
    excluded = UnitStockBatch.model_validate(stock.model_dump() | changes)
    with pytest.raises(UnitStockShortage) as error:
        plan((line(1, 1),), (excluded,))
    assert error.value.available_base_units == 0


def test_fefo_order_is_preserved_across_repeated_unit_lines() -> None:
    early = batch(2, config=packaging(10), expiry=5)
    late = batch(1, config=packaging(10), expiry=30)
    result = plan((line(1, 8), line(2, 5)), (late, early))
    assert result[0].plan.picks[0].batch_id == early.id
    assert [pick.batch_id for pick in result[1].plan.picks] == [early.id, late.id]
    assert [pick.base_units for pick in result[1].plan.picks] == [2, 3]
    assert result == plan((line(1, 8), line(2, 5)), (early, late))


@pytest.mark.parametrize("invalid", ["tenant", "branch", "batch_id", "pack_id"])
def test_global_scope_and_identity_are_validated_before_filtering(invalid: str) -> None:
    original = batch(config=packaging(10), packs=(opened(11, 3),))
    if invalid == "batch_id":
        snapshots = (original, original)
    elif invalid == "pack_id":
        snapshots = (original, batch(2, config=packaging(10), packs=(opened(11, 3),)))
    else:
        field = "tenant_id" if invalid == "tenant" else "branch_id"
        snapshots = (
            UnitStockBatch.model_validate(
                original.model_dump()
                | {field: UUID(int=999), "expires_at": TODAY - timedelta(days=1)}
            ),
        )
    with pytest.raises(ValueError):
        plan((line(1, 1),), snapshots)


def test_repeated_item_identifiers_are_rejected() -> None:
    with pytest.raises(ValueError, match="item identifiers"):
        plan((line(1, 1), line(1, 1)), (batch(config=packaging(10)),))


def test_different_catalogs_consume_their_own_stock() -> None:
    first = line(1, 10)
    second = UnitDraftQuoteLineV2.model_validate(
        line(2, 10).model_dump() | {"catalog_id": UUID(int=999)}
    )
    other_stock = UnitStockBatch.model_validate(
        batch(2, config=packaging(10)).model_dump() | {"catalog_id": second.catalog_id}
    )
    result = plan((first, second), (batch(1, config=packaging(10)), other_stock))
    assert [row.plan.picks[0].batch_id for row in result] == [UUID(int=1), UUID(int=2)]
