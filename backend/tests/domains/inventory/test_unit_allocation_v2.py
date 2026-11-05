"""Whole-cart previews preserve physical identities and never authorize posting."""

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta
from itertools import permutations
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.catalog.unit_sales import MAX_BASE_UNITS
from app.domains.inventory.unit_allocation_v2 import (
    UnitCartLineV2,
    UnitCartStockPlanV2,
    plan_unit_cart_stock_v2,
)
from app.domains.inventory.unit_stock_v2 import (
    OpenedBlisterV2,
    OpenedPackV2,
    SaleFormatV2,
    UnitPackagingV2,
    UnitStockBatchV2,
)

TODAY = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, tzinfo=UTC)
TENANT, BRANCH, CATALOG, OTHER_CATALOG = (UUID(int=i) for i in (101, 102, 103, 104))


def packaging(*, blister: int | None = 10, step: int = 1) -> UnitPackagingV2:
    return UnitPackagingV2(
        unit="tablet", units_per_pack=30, units_per_blister=blister, dispense_step=step
    )


def opened(
    number: int,
    *,
    intact: tuple[int, ...] = (),
    pieces: tuple[OpenedBlisterV2, ...] = (),
    loose: int = 0,
) -> OpenedPackV2:
    return OpenedPackV2(
        id=UUID(int=number),
        opened_at=NOW,
        intact_blisters=intact,
        opened_blisters=pieces,
        loose_units=loose,
    )


def batch(
    number: int = 1,
    *,
    closed: int = 1,
    packs: tuple[OpenedPackV2, ...] = (),
    config: UnitPackagingV2 | None = None,
    expiry: int = 30,
    catalog: UUID = CATALOG,
) -> UnitStockBatchV2:
    return UnitStockBatchV2(
        id=UUID(int=number),
        tenant_id=TENANT,
        branch_id=BRANCH,
        catalog_id=catalog,
        expires_at=TODAY + timedelta(days=expiry),
        created_at=NOW,
        packaging=config or packaging(),
        closed_packs=closed,
        opened_packs=packs,
    )


def line(
    number: int,
    sale_format: SaleFormatV2,
    quantity: int,
    *,
    config: UnitPackagingV2 | None = None,
    catalog: UUID = CATALOG,
) -> UnitCartLineV2:
    return UnitCartLineV2(
        id=UUID(int=number),
        catalog_id=catalog,
        packaging=config or packaging(),
        sale_format=sale_format,
        quantity=quantity,
    )


def plan(
    lines: tuple[UnitCartLineV2, ...], batches: tuple[UnitStockBatchV2, ...]
) -> UnitCartStockPlanV2:
    return plan_unit_cart_stock_v2(
        lines=lines, batches=batches, tenant_id=TENANT, branch_id=BRANCH, today=TODAY
    )


def remaining_units(batches: tuple[UnitStockBatchV2, ...]) -> int:
    return sum(
        item.closed_packs * item.packaging.units_per_pack
        + sum(
            len(pack.intact_blisters) * (item.packaging.units_per_blister or 0)
            + sum(piece.remaining_units for piece in pack.opened_blisters)
            + pack.loose_units
            for pack in item.opened_packs
        )
        for item in batches
    )


@pytest.mark.parametrize("order", tuple(permutations((0, 1, 2))))
def test_formats_reserve_whole_packs_before_blisters_and_units(order: tuple[int, ...]) -> None:
    requests = (line(201, "unit", 5), line(202, "blister", 1), line(203, "pack", 1))
    ordered = tuple(requests[index] for index in order)
    source = (batch(closed=2),)
    result = plan(ordered, source)
    assert tuple(item.line_id for item in result.lines) == tuple(item.id for item in ordered)
    allocations = {item.line_id: item for item in result.lines}
    assert result.allocation_order == (UUID(int=203), UUID(int=202), UUID(int=201))
    assert allocations[UUID(int=203)].base_units == 30
    assert sum(pick.closed_packs for pick in allocations[UUID(int=203)].picks) == 1
    assert allocations[UUID(int=202)].base_units == 10
    assert allocations[UUID(int=201)].base_units == 5
    assert remaining_units(result.residual_batches) == 15
    assert source[0].closed_packs == 2 and source[0].opened_packs == ()
    for allocated in result.lines:
        assert (
            remaining_units(allocated.before_batches) - remaining_units(allocated.after_batches)
            == allocated.base_units
        )
        assert sum(pick.base_units for pick in allocated.picks) == allocated.base_units


def test_unit_line_uses_original_fragment_before_cart_created_virtual_pack() -> None:
    original = opened(11, pieces=(OpenedBlisterV2(number=1, remaining_units=5),)).model_copy(
        update={"opened_at": NOW + timedelta(hours=1)}
    )
    source = (batch(closed=1, packs=(original,)),)
    result = plan((line(201, "blister", 1), line(202, "unit", 3)), source)
    assert {pick.opened_pack_id for pick in result.lines[1].picks} == {original.id}
    assert not any(pick.newly_opened for pick in result.lines[1].picks)
    residual = result.residual_batches[0]
    assert residual.closed_packs == 0
    assert len(residual.opened_packs) == 2
    existing = next(pack for pack in residual.opened_packs if pack.id == original.id)
    virtual = next(pack for pack in residual.opened_packs if pack.id != original.id)
    assert tuple(piece.remaining_units for piece in existing.opened_blisters) == (2,)
    assert len(virtual.intact_blisters) == 2
    assert virtual.opened_blisters == ()
    assert remaining_units(result.residual_batches) == 22
    assert result.lock_opened_pack_ids == (original.id,)


def test_later_line_reuses_one_newly_opened_pack_and_blister() -> None:
    source = (batch(),)
    result = plan((line(201, "unit", 3), line(202, "unit", 4)), source)
    residual = result.residual_batches[0]
    assert residual.closed_packs == 0
    assert len(residual.opened_packs) == 1
    pack = residual.opened_packs[0]
    assert len(pack.intact_blisters) == 2
    assert len(pack.opened_blisters) == 1
    assert pack.opened_blisters[0].remaining_units == 3
    assert remaining_units(result.residual_batches) == 23
    assert result.lock_batch_ids == (source[0].id,)
    assert result.lock_opened_pack_ids == ()
    assert plan((line(201, "unit", 3), line(202, "unit", 4)), source) == result
    assert any(pick.newly_opened for item in result.lines for pick in item.picks)


@pytest.mark.parametrize("sale_format,quantity", [("pack", 1), ("blister", 4)])
def test_full_opened_carton_cannot_supply_closed_pack_or_extra_blister(
    sale_format: SaleFormatV2, quantity: int
) -> None:
    source = (batch(closed=0, packs=(opened(11, intact=(1, 2, 3)),)),)
    with pytest.raises(ValueError):
        plan((line(201, sale_format, quantity),), source)
    result = plan((line(201, "blister", 3),), source)
    assert remaining_units(result.residual_batches) == 0


def test_fragments_cannot_be_combined_into_intact_blister() -> None:
    pieces = tuple(
        opened(number, pieces=(OpenedBlisterV2(number=1, remaining_units=5),))
        for number in (11, 12)
    )
    with pytest.raises(ValueError):
        plan((line(201, "blister", 1),), (batch(closed=0, packs=pieces),))


def test_each_opened_pack_respects_legacy_step_without_combining_small_remnants() -> None:
    config = packaging(step=6)
    pieces = tuple(
        opened(number, pieces=(OpenedBlisterV2(number=1, remaining_units=3),))
        for number in (11, 12)
    )
    with pytest.raises(ValueError):
        plan(
            (line(201, "unit", 6, config=config),), (batch(closed=0, packs=pieces, config=config),)
        )
    larger = opened(13, pieces=(OpenedBlisterV2(number=1, remaining_units=9),))
    result = plan(
        (line(201, "unit", 6, config=config),), (batch(closed=0, packs=(larger,), config=config),)
    )
    assert remaining_units(result.residual_batches) == 3


def test_legacy_step_can_span_multiple_blisters_in_the_same_opened_pack() -> None:
    config = packaging(step=15)
    source = (batch(closed=0, packs=(opened(11, intact=(1, 2, 3)),), config=config),)
    result = plan((line(201, "unit", 15, config=config),), source)
    assert remaining_units(result.residual_batches) == 15
    residual = result.residual_batches[0].opened_packs[0]
    assert len(residual.intact_blisters) == 1
    assert tuple(piece.remaining_units for piece in residual.opened_blisters) == (5,)


def test_fragments_inside_one_pack_can_jointly_satisfy_legacy_step() -> None:
    config = packaging(step=6)
    pack = opened(
        11,
        pieces=(
            OpenedBlisterV2(number=1, remaining_units=3),
            OpenedBlisterV2(number=2, remaining_units=3),
        ),
    )
    result = plan(
        (line(201, "unit", 6, config=config),), (batch(closed=0, packs=(pack,), config=config),)
    )
    assert remaining_units(result.residual_batches) == 0


def test_unknown_blister_composition_supports_loose_units_but_rejects_blisters() -> None:
    config = packaging(blister=None)
    source = (batch(closed=0, packs=(opened(11, loose=7),), config=config),)
    result = plan((line(201, "unit", 4, config=config),), source)
    assert remaining_units(result.residual_batches) == 3
    with pytest.raises(ValueError):
        plan((line(201, "blister", 1, config=config),), source)


def test_fefo_precedes_preference_for_already_opened_later_batch() -> None:
    early = batch(2, expiry=1)
    late = batch(1, closed=0, expiry=2, packs=(opened(11, intact=(1, 2, 3)),))
    result = plan((line(201, "unit", 4),), (late, early))
    assert {pick.batch_id for pick in result.lines[0].picks} == {early.id}
    assert remaining_units(result.residual_batches) == 56


def test_equal_expiry_and_creation_ties_are_resolved_by_batch_id() -> None:
    result = plan((line(201, "pack", 1),), (batch(2), batch(1)))
    assert {pick.batch_id for pick in result.lines[0].picks} == {UUID(int=1)}


@pytest.mark.parametrize("expiry,blocked", [(0, False), (-1, False), (1, True)])
def test_expired_or_blocked_stock_cannot_supply_cart(expiry: int, blocked: bool) -> None:
    source = batch(expiry=expiry).model_copy(update={"is_blocked": blocked})
    with pytest.raises(ValueError):
        plan((line(201, "unit", 1),), (source,))


def test_composition_mismatch_does_not_reinterpret_historical_stock() -> None:
    with pytest.raises(ValueError):
        plan((line(201, "unit", 1),), (batch(config=packaging(blister=None)),))


def test_repeated_lines_share_stock_and_failure_leaves_input_immutable() -> None:
    lines = (line(201, "pack", 1), line(202, "pack", 1))
    source = (batch(),)
    before = tuple(item.model_dump(mode="json") for item in source)
    with pytest.raises(ValueError):
        plan(lines, source)
    assert tuple(item.model_dump(mode="json") for item in source) == before
    assert tuple(item.quantity for item in lines) == (1, 1)


def test_catalogs_are_allocated_independently_without_borrowing_stock() -> None:
    source = (batch(1), batch(2, closed=2, catalog=OTHER_CATALOG))
    result = plan((line(201, "pack", 1), line(202, "pack", 2, catalog=OTHER_CATALOG)), source)
    assert {pick.batch_id for pick in result.lines[0].picks} == {UUID(int=1)}
    assert {pick.batch_id for pick in result.lines[1].picks} == {UUID(int=2)}
    with pytest.raises(ValueError):
        plan((line(201, "pack", 2), line(202, "pack", 1, catalog=OTHER_CATALOG)), source)


@pytest.mark.parametrize(
    "field,value", [("tenant_id", UUID(int=999)), ("branch_id", UUID(int=999))]
)
def test_scope_is_validated_even_for_expired_unrequested_catalog(field: str, value: UUID) -> None:
    outside = batch(2, expiry=-1, catalog=OTHER_CATALOG).model_copy(update={field: value})
    with pytest.raises(ValueError):
        plan((line(201, "unit", 1),), (batch(), outside))


def test_duplicate_ids_are_rejected_before_eligibility_filtering() -> None:
    requests = (line(201, "unit", 1),)
    valid = batch(packs=(opened(11, intact=(1,)),))
    duplicate_batch = batch(1, expiry=-1)
    duplicate_pack = batch(2, expiry=-1, packs=(opened(11, intact=(1,)),))
    for excluded in (duplicate_batch, duplicate_pack):
        with pytest.raises(ValueError):
            plan(requests, (valid, excluded))
    with pytest.raises(ValueError):
        plan((requests[0], requests[0]), (valid,))


def test_unrequested_catalog_is_rejected_even_when_expired() -> None:
    with pytest.raises(ValueError):
        plan((line(201, "unit", 1),), (batch(), batch(2, expiry=-1, catalog=OTHER_CATALOG)))


def test_model_copy_cannot_bypass_snapshot_or_cart_validation() -> None:
    malformed_stock = batch(2, expiry=-1).model_copy(update={"closed_packs": True})
    malformed_line = line(201, "unit", 1).model_copy(update={"quantity": True})
    for requests, source in (
        ((malformed_line,), (batch(),)),
        ((line(201, "unit", 1),), (batch(), malformed_stock)),
    ):
        with pytest.raises(ValueError):
            plan(requests, source)


@pytest.mark.parametrize("quantity", [True, False, "2", 2.0, 0, -1, MAX_BASE_UNITS + 1])
def test_line_quantity_requires_strict_positive_bounded_integer(quantity: object) -> None:
    data = line(201, "unit", 1).model_dump() | {"quantity": quantity}
    with pytest.raises(ValidationError):
        UnitCartLineV2.model_validate(data)


def test_cart_limit_accepts_200_lines_and_rejects_201() -> None:
    requests = tuple(line(200 + index, "unit", 1) for index in range(200))
    result = plan(requests, (batch(closed=7),))
    assert len(result.lines) == 200
    assert remaining_units(result.residual_batches) == 10
    with pytest.raises(ValueError):
        plan((*requests, line(400, "unit", 1)), (batch(closed=7),))


def test_huge_pack_count_uses_compact_allocations_without_materializing_packs() -> None:
    count = MAX_BASE_UNITS // 30
    result = plan((line(201, "pack", count),), (batch(closed=count),))
    assert len(result.lines[0].picks) == 1
    assert result.lines[0].picks[0].closed_packs == count
    assert remaining_units(result.residual_batches) == 0


def test_overflow_and_runtime_container_types_are_rejected() -> None:
    requests = (line(201, "pack", MAX_BASE_UNITS),)
    with pytest.raises(ValueError):
        plan(requests, (batch(),))
    count = MAX_BASE_UNITS // 30
    with pytest.raises(ValueError):
        plan((line(201, "unit", 1),), (batch(1, closed=count), batch(2, closed=count)))
    arguments = {
        "lines": (line(201, "unit", 1),),
        "batches": (batch(),),
        "tenant_id": TENANT,
        "branch_id": BRANCH,
        "today": TODAY,
    }
    for field, value in (
        ("lines", []),
        ("batches", []),
        ("tenant_id", str(TENANT)),
        ("branch_id", str(BRANCH)),
        ("today", NOW),
    ):
        with pytest.raises(ValueError):
            plan_unit_cart_stock_v2(**(arguments | {field: value}))


def test_preview_result_and_input_models_are_frozen() -> None:
    source = batch()
    result = plan((line(201, "unit", 1),), (source,))
    with pytest.raises(FrozenInstanceError):
        result.lines = ()
    with pytest.raises(ValidationError):
        source.closed_packs = 0
