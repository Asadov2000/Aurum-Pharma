"""Pure allocation contracts; scope validation here does not prove database RLS."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.inventory.unit_stock import (
    MAX_BASE_UNITS,
    OpenedPack,
    UnitPackaging,
    UnitStockBatch,
    UnitStockPlan,
    UnitStockShortage,
    plan_unit_stock,
)

TODAY = date(2026, 10, 4)
NOW = datetime(2026, 10, 4, tzinfo=UTC)
TENANT, BRANCH, CATALOG = (UUID(int=i) for i in (101, 102, 103))


def packaging(factor: int = 30, step: int = 1) -> UnitPackaging:
    return UnitPackaging(version=1, unit="tablet", units_per_pack=factor, dispense_step=step)


def opened(number: int, units: int, *, age: int = 0) -> OpenedPack:
    return OpenedPack(
        id=UUID(int=number), opened_at=NOW - timedelta(days=age), remaining_units=units
    )


def batch(
    number: int = 1,
    *,
    closed: int = 1,
    packs: tuple[OpenedPack, ...] = (),
    config: UnitPackaging | None = None,
    expiry: int = 30,
    age: int = 0,
    blocked: bool = False,
) -> UnitStockBatch:
    return UnitStockBatch(
        id=UUID(int=number),
        tenant_id=TENANT,
        branch_id=BRANCH,
        catalog_id=CATALOG,
        expires_at=TODAY + timedelta(days=expiry),
        created_at=NOW - timedelta(days=age),
        packaging=config or packaging(),
        closed_packs=closed,
        opened_packs=packs,
        is_blocked=blocked,
    )


def allocate(
    batches: tuple[UnitStockBatch, ...],
    quantity: int,
    *,
    whole: bool = False,
    config: UnitPackaging | None = None,
) -> UnitStockPlan:
    return plan_unit_stock(
        batches=batches,
        tenant_id=TENANT,
        branch_id=BRANCH,
        catalog_id=CATALOG,
        today=TODAY,
        sale_format="pack" if whole else "unit",
        quantity=quantity,
        packaging=config or packaging(),
    )


def balance(snapshot: UnitStockBatch) -> int:
    return snapshot.closed_packs * snapshot.packaging.units_per_pack + sum(
        pack.remaining_units for pack in snapshot.opened_packs
    )


@pytest.mark.parametrize("factor", [3, 7, 30])
def test_single_sales_conserve_stock_without_mutating_snapshots(factor: int) -> None:
    config = packaging(factor)
    original = batch(config=config)
    snapshot = original
    openings = 0
    for number in range(factor):
        before = balance(snapshot)
        plan = allocate((snapshot,), 1, config=config)
        pick = plan.picks[0]
        assert plan.base_units == pick.base_units == 1
        assert plan.quantity == 1 and plan.sale_format == "unit"
        openings += pick.new_packs_opened
        remainders = {take.pack_id: take.remaining_units for take in pick.opened_packs}
        remaining = tuple(
            OpenedPack.model_validate(
                pack.model_dump()
                | {"remaining_units": remainders.get(pack.id, pack.remaining_units)}
            )
            for pack in snapshot.opened_packs
            if remainders.get(pack.id, pack.remaining_units) > 0
        )
        if pick.new_opened_remainder_units:
            remaining += (opened(1000 + number, pick.new_opened_remainder_units),)
        snapshot = UnitStockBatch.model_validate(
            snapshot.model_dump()
            | {
                "closed_packs": snapshot.closed_packs - pick.new_packs_opened,
                "opened_packs": remaining,
            }
        )
        assert before - balance(snapshot) == plan.base_units
        assert original.closed_packs == 1 and original.opened_packs == ()
    assert balance(snapshot) == 0
    assert openings == 1


def test_opened_stock_cannot_be_reassembled_into_a_closed_pack() -> None:
    stock = batch(closed=0, packs=(opened(11, 5), opened(12, 5)), config=packaging(10))
    with pytest.raises(UnitStockShortage) as caught:
        allocate((stock,), 1, whole=True, config=packaging(10))
    assert caught.value.requested_base_units == 10
    assert caught.value.available_base_units == 0


def test_fefo_precedes_opened_pack_preference_between_batches() -> None:
    early, later = batch(2, expiry=10), batch(1, closed=0, packs=(opened(11, 9),))
    plan = allocate((later, early), 32)
    assert [pick.batch_id for pick in plan.picks] == [early.id, later.id]
    assert plan.picks[0].new_packs_opened == 1
    assert plan.picks[1].opened_packs[0].units_taken == 2
    assert plan.lock_batch_ids == (later.id, early.id)


def test_order_is_deterministic_with_all_fefo_ties_and_opened_pack_ties() -> None:
    older = batch(9, closed=0, packs=(opened(22, 2), opened(21, 2)), age=1)
    second, third = batch(2, closed=0, packs=(opened(20, 2),)), batch(3)
    forward = allocate((third, second, older), 7)
    assert forward == allocate((older, third, second), 7)
    assert [pick.batch_id for pick in forward.picks] == [older.id, second.id, third.id]
    assert [take.pack_id for take in forward.picks[0].opened_packs] == [UUID(int=21), UUID(int=22)]
    assert forward.lock_batch_ids == (second.id, third.id, older.id)
    assert forward.lock_opened_pack_ids == tuple(UUID(int=i) for i in (20, 21, 22))


def test_oldest_opened_pack_is_used_before_closed_stock_within_batch() -> None:
    stock = batch(packs=(opened(11, 4), opened(12, 3, age=1)))
    plan = allocate((stock,), 9)
    pick = plan.picks[0]
    assert [take.pack_id for take in pick.opened_packs] == [UUID(int=12), UUID(int=11)]
    assert [take.remaining_units for take in pick.opened_packs] == [0, 0]
    assert pick.new_packs_opened == 1 and pick.new_opened_remainder_units == 28
    assert pick.closed_packs_taken == 0 and pick.base_units == 9


def test_pack_sale_only_consumes_closed_packs() -> None:
    stock = batch(closed=3, packs=(opened(11, 29),))
    plan = allocate((stock,), 2, whole=True)
    pick = plan.picks[0]
    assert pick.closed_packs_taken == 2 and pick.base_units == plan.base_units == 60
    assert pick.opened_packs == () and plan.lock_opened_pack_ids == (UUID(int=11),)
    assert pick.new_packs_opened == pick.new_opened_remainder_units == 0


def test_dispense_step_applies_to_each_opened_pack() -> None:
    config = packaging(6, 2)
    stock = batch(closed=0, packs=(opened(11, 3), opened(12, 3)), config=config)
    with pytest.raises(UnitStockShortage) as caught:
        allocate((stock,), 6, config=config)
    assert caught.value.available_base_units == 4
    pick = allocate((stock,), 4, config=config).picks[0]
    assert [take.units_taken for take in pick.opened_packs] == [2, 2]
    assert [take.remaining_units for take in pick.opened_packs] == [1, 1]
    with pytest.raises(ValueError):
        allocate((stock,), 3, config=config)


def test_step_remnants_are_not_combined_even_when_closed_stock_exists() -> None:
    config = packaging(6, 2)
    stock = batch(packs=(opened(11, 1), opened(12, 1)), config=config)
    pick = allocate((stock,), 2, config=config).picks[0]
    assert pick.opened_packs == ()
    assert pick.new_packs_opened == 1 and pick.new_opened_remainder_units == 4


def test_shortage_is_all_or_nothing_and_reports_usable_stock() -> None:
    stock = batch(closed=0, packs=(opened(11, 4),))
    before = stock.model_dump()
    with pytest.raises(UnitStockShortage) as caught:
        allocate((stock,), 5)
    assert caught.value.requested_base_units == 5
    assert caught.value.available_base_units == 4
    assert stock.model_dump() == before
    with pytest.raises(UnitStockShortage) as empty:
        allocate((), 1)
    assert empty.value.available_base_units == 0


def test_blocked_expired_and_incompatible_batches_are_excluded() -> None:
    eligible = batch(5)
    ignored = (
        batch(1, expiry=-1),
        batch(2, expiry=0),
        batch(3, blocked=True),
        batch(4, config=packaging(10), packs=(opened(11, 4),)),
    )
    plan = allocate((*ignored, eligible), 30)
    assert tuple(pick.batch_id for pick in plan.picks) == (eligible.id,)
    assert plan.lock_batch_ids == tuple(UUID(int=i) for i in range(1, 6))
    assert plan.lock_opened_pack_ids == (UUID(int=11),)
    with pytest.raises(UnitStockShortage) as caught:
        allocate(ignored, 1)
    assert caught.value.available_base_units == 0


@pytest.mark.parametrize("field", ["tenant_id", "branch_id", "catalog_id"])
def test_foreign_scope_fails_closed_even_for_unused_or_excluded_stock(field: str) -> None:
    foreign = UnitStockBatch.model_validate(
        batch(2, blocked=True).model_dump() | {field: UUID(int=999)}
    )
    with pytest.raises(ValueError):
        allocate((batch(1), foreign), 1)


def test_duplicate_batch_and_global_opened_pack_ids_fail_closed() -> None:
    first = batch(1, packs=(opened(11, 5),))
    with pytest.raises(ValueError):
        allocate((first, first), 1)
    with pytest.raises(ValueError):
        allocate((first, batch(2, packs=(opened(11, 3),), blocked=True)), 1)
    with pytest.raises(ValidationError):
        batch(packs=(opened(11, 3), opened(11, 3)))


@pytest.mark.parametrize("field", ["version", "units_per_pack", "dispense_step"])
@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_packaging_integers_are_strict(field: str, value: object) -> None:
    payload = packaging().model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        UnitPackaging.model_validate(payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"version": 2},
        {"units_per_pack": 1},
        {"units_per_pack": 10001},
        {"dispense_step": 0},
        {"dispense_step": 7},
        {"unit": ""},
    ],
)
def test_packaging_rejects_unsupported_versions_and_invalid_boundaries(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        UnitPackaging.model_validate(packaging().model_dump() | overrides)


@pytest.mark.parametrize("value", [True, 1.5, "1", 0, -1])
def test_quantity_is_strict_and_positive(value: int) -> None:
    with pytest.raises(ValueError):
        plan_unit_stock(
            batches=(batch(),),
            tenant_id=TENANT,
            branch_id=BRANCH,
            catalog_id=CATALOG,
            today=TODAY,
            sale_format="unit",
            quantity=value,
            packaging=packaging(),
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("closed_packs", True),
        ("closed_packs", 1.0),
        ("closed_packs", "1"),
        ("closed_packs", -1),
        ("is_blocked", "false"),
        ("created_at", datetime(2026, 10, 4)),
    ],
)
def test_batch_rejects_invalid_types_and_naive_timestamps(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        UnitStockBatch.model_validate(batch().model_dump() | {field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("remaining_units", True),
        ("remaining_units", 1.0),
        ("remaining_units", "1"),
        ("remaining_units", 0),
        ("opened_at", datetime(2026, 10, 4)),
    ],
)
def test_opened_pack_rejects_invalid_types_and_naive_timestamps(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        OpenedPack.model_validate(opened(11, 1).model_dump() | {field: value})


def test_opened_pack_must_be_smaller_than_its_original_package() -> None:
    with pytest.raises(ValidationError):
        batch(packs=(opened(11, 30),))


def test_maximum_stock_and_bulk_allocation_use_integer_arithmetic() -> None:
    config = packaging(2)
    closed_count = MAX_BASE_UNITS // 2
    stock = batch(closed=closed_count, config=config)
    plan = allocate((stock,), closed_count, whole=True, config=config)
    assert plan.base_units == closed_count * 2
    assert plan.picks[0].closed_packs_taken == closed_count
    unit_plan = allocate((stock,), closed_count * 2, config=config)
    assert unit_plan.picks[0].new_packs_opened == closed_count
    assert unit_plan.picks[0].new_opened_remainder_units == 0
    with pytest.raises(ValidationError):
        batch(closed=closed_count + 1, config=config)
    with pytest.raises(ValidationError):
        batch(closed=closed_count, config=config, packs=(opened(11, 1), opened(12, 1)))
    with pytest.raises(ValueError):
        allocate((stock,), MAX_BASE_UNITS + 1, config=config)


def test_models_and_nested_plan_results_are_frozen() -> None:
    stock = batch(packs=(opened(11, 5),))
    plan = allocate((stock,), 2)
    for model, field in (
        (stock.packaging, "version"),
        (stock, "closed_packs"),
        (stock.opened_packs[0], "remaining_units"),
    ):
        with pytest.raises(ValidationError):
            setattr(model, field, 2)
    for result, field in (
        (plan, "quantity"),
        (plan.picks[0], "base_units"),
        (plan.picks[0].opened_packs[0], "units_taken"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(result, field, 2)
