"""Physical write-offs remove explicit damaged stock without sale eligibility rules."""

from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.catalog.unit_sales import MAX_BASE_UNITS
from app.domains.inventory.unit_stock_v2 import OpenedBlisterV2, UnitStockBatchV2
from app.domains.inventory.unit_writeoff_v2 import (
    PhysicalBlisterWriteOffV2,
    PhysicalPackWriteOffV2,
    PhysicalWriteOffPlanV2,
    PhysicalWriteOffSelectionV2,
    plan_physical_writeoff_v2,
)
from tests.domains.inventory.test_unit_stock_v2 import BRANCH, TENANT, batch, opened, packaging


def pack_selection(
    pack_id: UUID, *blisters: tuple[int, int], loose: int = 0
) -> PhysicalPackWriteOffV2:
    return PhysicalPackWriteOffV2(
        pack_id=pack_id,
        blisters=tuple(PhysicalBlisterWriteOffV2(number=n, units=u) for n, u in blisters),
        loose_units=loose,
    )


def plan(stock: UnitStockBatchV2, selection: PhysicalWriteOffSelectionV2) -> PhysicalWriteOffPlanV2:
    return plan_physical_writeoff_v2(
        batch=stock, tenant_id=TENANT, branch_id=BRANCH, selection=selection
    )


def test_mixed_writeoff_preserves_pack_and_blister_identities_without_mutating_inputs() -> None:
    first = opened(
        11,
        intact=(2,),
        pieces=(
            OpenedBlisterV2(number=3, remaining_units=5),
            OpenedBlisterV2(number=1, remaining_units=7),
        ),
    )
    second = opened(12, intact=(3, 1), pieces=(OpenedBlisterV2(number=2, remaining_units=4),))
    stock = batch(closed=2, packs=(second, first))
    selection = PhysicalWriteOffSelectionV2(
        closed_packs=1,
        opened_packs=(
            pack_selection(second.id, (3, 10), (2, 3)),
            pack_selection(first.id, (2, 4), (1, 2)),
        ),
    )
    original_stock, original_selection = stock.model_dump(), selection.model_dump()
    result = plan(stock, selection)
    assert result.before == stock
    assert result.before.opened_packs == (second, first)
    assert result.base_units == 49
    assert result.after.closed_packs == 1
    assert result.after.opened_packs == (
        opened(
            11,
            pieces=(
                OpenedBlisterV2(number=1, remaining_units=5),
                OpenedBlisterV2(number=2, remaining_units=6),
                OpenedBlisterV2(number=3, remaining_units=5),
            ),
        ),
        opened(12, intact=(1,), pieces=(OpenedBlisterV2(number=2, remaining_units=1),)),
    )
    assert result.after.model_dump(exclude={"closed_packs", "opened_packs"}) == stock.model_dump(
        exclude={"closed_packs", "opened_packs"}
    )
    assert stock.model_dump() == original_stock
    assert selection.model_dump() == original_selection
    with pytest.raises(FrozenInstanceError):
        result.base_units = 0


def test_selection_order_does_not_change_the_plan_and_remaining_blisters_are_sorted() -> None:
    first = opened(11, intact=(3, 1), pieces=(OpenedBlisterV2(number=2, remaining_units=5),))
    second = opened(12, intact=(3, 2, 1))
    stock = batch(closed=0, packs=(second, first))
    forward = PhysicalWriteOffSelectionV2(
        opened_packs=(pack_selection(first.id, (1, 2), (2, 1)), pack_selection(second.id, (2, 3))),
    )
    backward = PhysicalWriteOffSelectionV2(
        opened_packs=(pack_selection(second.id, (2, 3)), pack_selection(first.id, (2, 1), (1, 2))),
    )
    left, right = plan(stock, forward), plan(stock, backward)
    assert left == right
    assert left.base_units == 6
    assert [pack.id for pack in left.after.opened_packs] == [first.id, second.id]
    assert left.after.opened_packs[1].intact_blisters == (1, 3)
    assert [part.number for part in left.after.opened_packs[0].opened_blisters] == [1, 2]
    assert left.before == stock


def test_untouched_pack_retains_its_exact_historical_blister_state() -> None:
    target = opened(11, intact=(1,))
    untouched = opened(12, intact=(3, 1), pieces=(OpenedBlisterV2(number=2, remaining_units=5),))
    result = plan(
        batch(closed=0, packs=(untouched, target)),
        PhysicalWriteOffSelectionV2(opened_packs=(pack_selection(target.id, (1, 1)),)),
    )
    assert result.after.opened_packs[1] == untouched
    assert result.after.opened_packs[1].intact_blisters == (3, 1)


@pytest.mark.parametrize("units", [1, 5])
def test_unknown_composition_removes_only_selected_loose_units(units: int) -> None:
    target, untouched = opened(11, loose=5), opened(12, loose=7)
    stock = batch(closed=1, packs=(untouched, target), config=packaging(blister=None))
    result = plan(
        stock, PhysicalWriteOffSelectionV2(opened_packs=(pack_selection(target.id, loose=units),))
    )
    assert result.base_units == units and result.after.closed_packs == 1
    expected = ((opened(11, loose=5 - units),) if units < 5 else ()) + (untouched,)
    assert result.after.opened_packs == expected
    assert result.before.opened_packs == (untouched, target)


def test_exhausting_an_opened_blister_removes_it_without_recombining_other_fragments() -> None:
    target = opened(
        11,
        pieces=(
            OpenedBlisterV2(number=2, remaining_units=5),
            OpenedBlisterV2(number=1, remaining_units=5),
        ),
    )
    result = plan(
        batch(closed=0, packs=(target,)),
        PhysicalWriteOffSelectionV2(opened_packs=(pack_selection(target.id, (1, 5)),)),
    )
    assert result.base_units == 5
    assert result.after.opened_packs == (
        opened(11, pieces=(OpenedBlisterV2(number=2, remaining_units=5),)),
    )
    assert result.after.opened_packs[0].intact_blisters == ()


def test_exhausting_all_opened_stock_returns_an_empty_valid_snapshot() -> None:
    target = opened(11, intact=(1,), pieces=(OpenedBlisterV2(number=2, remaining_units=3),))
    result = plan(
        batch(closed=1, packs=(target,)),
        PhysicalWriteOffSelectionV2(
            closed_packs=1, opened_packs=(pack_selection(target.id, (2, 3), (1, 10)),)
        ),
    )
    assert result.base_units == 43
    assert result.after.closed_packs == 0 and result.after.opened_packs == ()


@pytest.mark.parametrize("blocked,expiry", [(True, 30), (False, -1), (True, -1)])
def test_damage_can_remove_expired_or_blocked_stock_without_claiming_authorization(
    blocked: bool, expiry: int
) -> None:
    stock = batch(blocked=blocked, expiry=expiry)
    result = plan(stock, PhysicalWriteOffSelectionV2(closed_packs=1))
    assert result.base_units == 30
    assert result.after.is_blocked == blocked and result.after.expires_at == stock.expires_at


def test_damage_of_one_unit_does_not_use_the_sales_dispense_step() -> None:
    target = opened(11, intact=(1, 2, 3))
    result = plan(
        batch(closed=0, packs=(target,), config=packaging(step=6)),
        PhysicalWriteOffSelectionV2(opened_packs=(pack_selection(target.id, (2, 1)),)),
    )
    assert result.base_units == 1
    assert result.after.opened_packs == (
        opened(11, intact=(1, 3), pieces=(OpenedBlisterV2(number=2, remaining_units=9),)),
    )


def test_closed_packs_are_aggregated_and_never_materialized_as_opened_packs() -> None:
    maximum = MAX_BASE_UNITS // 30
    result = plan(batch(closed=maximum), PhysicalWriteOffSelectionV2(closed_packs=maximum))
    assert result.base_units == maximum * 30
    assert result.after.closed_packs == 0 and result.after.opened_packs == ()


@pytest.mark.parametrize("field", ["tenant_id", "branch_id"])
def test_scope_mismatch_is_rejected_before_any_stock_change(field: str) -> None:
    stock = batch().model_copy(update={field: UUID(int=999)})
    before = stock.model_dump()
    with pytest.raises(ValueError):
        plan(stock, PhysicalWriteOffSelectionV2(closed_packs=1))
    assert stock.model_dump() == before


@pytest.mark.parametrize("field", ["tenant_id", "branch_id"])
def test_untyped_trusted_scope_is_not_silently_coerced(field: str) -> None:
    arguments: dict[str, object] = {"tenant_id": TENANT, "branch_id": BRANCH}
    arguments[field] = str(arguments[field])
    with pytest.raises(ValueError):
        plan_physical_writeoff_v2(
            batch=batch(), selection=PhysicalWriteOffSelectionV2(closed_packs=1), **arguments
        )


@pytest.mark.parametrize("kind", ["empty", "unknown", "closed overdraw", "opened overdraw"])
def test_missing_or_excess_stock_cannot_be_inferred_from_other_packs(kind: str) -> None:
    target = opened(11, loose=5)
    stock = batch(closed=1, packs=(target,), config=packaging(blister=None))
    selections = {
        "empty": PhysicalWriteOffSelectionV2(),
        "unknown": PhysicalWriteOffSelectionV2(
            opened_packs=(pack_selection(UUID(int=999), loose=1),)
        ),
        "closed overdraw": PhysicalWriteOffSelectionV2(closed_packs=2),
        "opened overdraw": PhysicalWriteOffSelectionV2(
            opened_packs=(pack_selection(target.id, loose=6),)
        ),
    }
    before = stock.model_dump()
    with pytest.raises(ValueError):
        plan(stock, selections[kind])
    assert stock.model_dump() == before


@pytest.mark.parametrize("kind", ["unknown number", "intact overdraw", "opened overdraw"])
def test_a_blister_writeoff_cannot_spill_into_another_blister(kind: str) -> None:
    target = opened(11, intact=(1,), pieces=(OpenedBlisterV2(number=2, remaining_units=3),))
    choices = {"unknown number": (3, 1), "intact overdraw": (1, 11), "opened overdraw": (2, 4)}
    number, units = choices[kind]
    stock = batch(closed=1, packs=(target,))
    with pytest.raises(ValueError):
        plan(
            stock,
            PhysicalWriteOffSelectionV2(opened_packs=(pack_selection(target.id, (number, units)),)),
        )
    assert stock.opened_packs == (target,) and stock.closed_packs == 1


@pytest.mark.parametrize("known", [True, False])
def test_selection_format_must_match_historical_composition(known: bool) -> None:
    target = opened(11, intact=(1,)) if known else opened(11, loose=5)
    selected = pack_selection(target.id, loose=1) if known else pack_selection(target.id, (1, 1))
    stock = batch(packs=(target,), config=packaging(blister=10 if known else None))
    with pytest.raises(ValueError):
        plan(stock, PhysicalWriteOffSelectionV2(opened_packs=(selected,)))


def test_an_unknown_pack_selection_does_not_automatically_open_a_sealed_pack() -> None:
    stock = batch(closed=2)
    with pytest.raises(ValueError):
        plan(
            stock,
            PhysicalWriteOffSelectionV2(opened_packs=(pack_selection(UUID(int=11), (1, 1)),)),
        )
    assert stock.closed_packs == 2 and stock.opened_packs == ()


@pytest.mark.parametrize("kind", ["pack", "blister", "noop"])
def test_duplicate_or_noop_selection_is_rejected(kind: str) -> None:
    target = opened(11, intact=(1,))
    payload = {
        "pack": {"opened_packs": [pack_selection(target.id, (1, 1)).model_dump()] * 2},
        "blister": {
            "opened_packs": [{"pack_id": target.id, "blisters": [{"number": 1, "units": 1}] * 2}]
        },
        "noop": {"closed_packs": 1, "opened_packs": [{"pack_id": target.id}]},
    }[kind]
    with pytest.raises(ValueError):
        selection = PhysicalWriteOffSelectionV2.model_validate(payload)
        plan(batch(packs=(target,)), selection)


@pytest.mark.parametrize("field", ["number", "units"])
@pytest.mark.parametrize("value", [True, False, "1", 1.5, 0, -1])
def test_blister_selection_requires_strict_positive_integers(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        PhysicalBlisterWriteOffV2.model_validate({"number": 1, "units": 1} | {field: value})


def test_blister_unit_count_is_bounded_by_the_physical_ledger_limit() -> None:
    with pytest.raises(ValidationError):
        PhysicalBlisterWriteOffV2(number=1, units=MAX_BASE_UNITS + 1)


@pytest.mark.parametrize("value", [True, False, "1", 1.5, -1])
@pytest.mark.parametrize("field", ["closed_packs", "loose_units"])
def test_pack_counts_require_strict_nonnegative_integers(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        if field == "closed_packs":
            PhysicalWriteOffSelectionV2.model_validate({field: value})
        else:
            PhysicalPackWriteOffV2.model_validate({"pack_id": UUID(int=11), field: value})


@pytest.mark.parametrize("model", ["blister", "pack", "selection"])
def test_selection_contracts_reject_extra_authority_or_money_fields(model: str) -> None:
    payloads = {
        "blister": {"number": 1, "units": 1},
        "pack": {"pack_id": UUID(int=11), "loose_units": 1},
        "selection": {"closed_packs": 1},
    }
    models = {
        "blister": PhysicalBlisterWriteOffV2,
        "pack": PhysicalPackWriteOffV2,
        "selection": PhysicalWriteOffSelectionV2,
    }
    with pytest.raises(ValidationError):
        models[model].model_validate(payloads[model] | {"cost_amount": "10.00"})


@pytest.mark.parametrize("kind", ["batch count", "packaging", "pack state", "duplicate source"])
def test_forged_nested_stock_models_are_revalidated(kind: str) -> None:
    target = opened(11, loose=5)
    stock = batch(closed=1, packs=(target,), config=packaging(blister=None))
    if kind == "batch count":
        stock = stock.model_copy(update={"closed_packs": True})
    elif kind == "packaging":
        stock = stock.model_copy(
            update={"packaging": stock.packaging.model_copy(update={"units_per_pack": True})}
        )
    elif kind == "pack state":
        stock = stock.model_copy(
            update={"opened_packs": (target.model_copy(update={"loose_units": -1}),)}
        )
    else:
        stock = stock.model_copy(update={"opened_packs": (target, target)})
    with pytest.raises(ValueError):
        plan(stock, PhysicalWriteOffSelectionV2(closed_packs=1))


@pytest.mark.parametrize("construction", ["copy", "construct"])
def test_forged_nested_selection_models_are_revalidated(construction: str) -> None:
    target = opened(11, intact=(1,))
    valid = PhysicalBlisterWriteOffV2(number=1, units=1)
    forged = (
        valid.model_copy(update={"units": True})
        if construction == "copy"
        else PhysicalBlisterWriteOffV2.model_construct(number=1, units=True)
    )
    selection = PhysicalWriteOffSelectionV2.model_construct(
        closed_packs=0,
        opened_packs=(
            PhysicalPackWriteOffV2.model_construct(
                pack_id=target.id, blisters=(forged,), loose_units=0
            ),
        ),
    )
    with pytest.raises(ValueError):
        plan(batch(closed=0, packs=(target,)), selection)
