"""Pure transfer plans conserve exact quantities and explicit historical value."""

from dataclasses import FrozenInstanceError
from decimal import Decimal, Inexact, InvalidOperation, Rounded, localcontext
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.catalog.unit_sales import MAX_BASE_UNITS, MAX_MONEY
from app.domains.inventory.unit_legacy_reconciliation import (
    LegacyReconciliationObservation,
    LegacyReconciliationSource,
)
from app.domains.inventory.unit_legacy_transition import (
    LegacyTransitionPlan,
    plan_legacy_transition,
)
from app.domains.inventory.unit_stock_v2 import OpenedBlisterV2, UnitPackagingV2
from tests.domains.inventory.test_unit_legacy_reconciliation import (
    BRANCH,
    TENANT,
    observation,
    opened,
    source,
)

RECORD = UUID(int=301)


def plan(
    original: LegacyReconciliationSource,
    counted: LegacyReconciliationObservation,
    cost: Decimal | None = Decimal("10.00"),
    carried: Decimal | None = Decimal("10.00"),
) -> LegacyTransitionPlan:
    return plan_legacy_transition(
        original,
        counted,
        tenant_id=TENANT,
        branch_id=BRANCH,
        reconciliation_id=RECORD,
        historical_pack_cost=cost,
        carried_cost=carried,
    )


@pytest.mark.parametrize("size", [3, 7, 30])
@pytest.mark.parametrize("cost", [Decimal("0.00"), Decimal("0.01"), Decimal("10.00")])
def test_closed_pack_exact_plan_preserves_both_sides(size: int, cost: Decimal) -> None:
    original, counted = source("1.000"), observation(size=size)
    result = plan(original, counted, cost, cost)
    assert result.legacy_quantity_delta == Decimal("-1.000")
    assert result.total_base_units == size
    assert result.total_cost == result.historical_pack_cost == cost
    assert result.closed_range is not None
    assert result.closed_range.pack_ordinal_start == result.closed_range.pack_count == 1
    assert result.closed_range.base_units == size
    assert result.closed_range.amount == cost
    assert result.opened_slices == ()
    assert result.source == original and result.observation == counted
    assert result.reconciliation_id == RECORD
    assert result.version == result.allocation_version == 1 and result.currency == "TJS"
    with pytest.raises(FrozenInstanceError):
        result.total_cost = Decimal("20.00")


def test_multiple_opened_packs_have_stable_ordinals_without_rewriting_composition() -> None:
    first = opened(4).model_copy(update={"id": UUID(int=401)})
    second = opened(4).model_copy(update={"id": UUID(int=402)})
    counted = observation(closed=1, packs=(second, first), size=10)
    before = counted.model_dump()
    result = plan(source("1.800"), counted, Decimal("0.05"), Decimal("0.09"))
    assert result.closed_range is not None and result.closed_range.amount == Decimal("0.05")
    assert [part.pack_id for part in result.opened_slices] == [first.id, second.id]
    assert [part.pack_ordinal for part in result.opened_slices] == [2, 3]
    assert all(part.unit_offset == 6 and part.base_units == 4 for part in result.opened_slices)
    assert all(part.amount == Decimal("0.02") for part in result.opened_slices)
    assert result.total_base_units == 18 and result.total_cost == Decimal("0.09")
    assert counted.model_dump() == result.observation.model_dump() == before
    reordered = plan(
        source("1.800"),
        observation(closed=1, packs=(first, second), size=10),
        Decimal("0.05"),
        Decimal("0.09"),
    )
    assert reordered.opened_slices == result.opened_slices
    assert reordered.closed_range == result.closed_range


def test_known_blister_composition_and_full_opened_pack_stay_opened() -> None:
    pack = opened(0).model_copy(
        update={
            "intact_blisters": (3, 1),
            "opened_blisters": (OpenedBlisterV2(number=2, remaining_units=4),),
        }
    )
    counted = observation(closed=0).model_copy(
        update={
            "packaging": UnitPackagingV2(
                unit="tablet", units_per_pack=30, units_per_blister=10, dispense_step=1
            ),
            "opened_packs": (pack,),
        }
    )
    result = plan(source("0.800"), counted, Decimal("10.00"), Decimal("8.00"))
    assert result.closed_range is None
    assert result.opened_slices[0].unit_offset == 6
    assert result.opened_slices[0].base_units == 24
    assert result.observation == counted
    full = plan(source(), observation(closed=0, packs=(opened(30),)))
    assert full.closed_range is None
    assert full.opened_slices[0].unit_offset == 0
    assert full.opened_slices[0].amount == Decimal("10.00")


@pytest.mark.parametrize("quantity,closed", [("0", 0), ("0.999", 1), ("1", 2), ("2", 1)])
def test_nonpositive_fractional_and_discrepant_counts_cannot_be_planned(
    quantity: str, closed: int
) -> None:
    with pytest.raises(ValueError, match="matching positive exact"):
        plan(source(quantity), observation(closed=closed))


def test_exact_units_do_not_allow_rounding_fractional_cents() -> None:
    counted = observation(closed=0, packs=(opened(15),))
    for carried in (Decimal("3.65"), Decimal("3.66")):
        with pytest.raises(ValueError, match="whole cents"):
            plan(source("0.500"), counted, Decimal("7.31"), carried)


def test_exact_legacy_money_must_also_match_each_physical_pack_tail() -> None:
    packs = tuple(opened(2).model_copy(update={"id": UUID(int=500 + i)}) for i in range(5))
    with pytest.raises(ValueError, match="layout cannot preserve"):
        plan(
            source("1.000"),
            observation(closed=0, packs=packs, size=10),
            Decimal("0.01"),
            Decimal("0.01"),
        )


@pytest.mark.parametrize("field", ["historical", "carried"])
@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        1.0,
        "10.00",
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-0.01"),
        Decimal("0.001"),
    ],
)
def test_invalid_or_unknown_money_is_not_coerced(field: str, value: object) -> None:
    bad = cast(Decimal | None, value)
    with pytest.raises(ValueError):
        plan(
            source(),
            observation(),
            bad if field == "historical" else Decimal("10.00"),
            bad if field == "carried" else Decimal("10.00"),
        )


def test_carried_cost_must_equal_exact_historical_valuation() -> None:
    with pytest.raises(ValueError, match="carried cost differs"):
        plan(source(), observation(), Decimal("10.00"), Decimal("9.99"))


@pytest.mark.parametrize("field", ["tenant_id", "branch_id", "catalog_id", "batch_id"])
def test_cross_identity_observation_is_rejected(field: str) -> None:
    with pytest.raises(ValueError):
        plan(source(), observation().model_copy(update={field: UUID(int=999)}))


@pytest.mark.parametrize("field", ["tenant_id", "branch_id"])
def test_source_and_count_cannot_override_trusted_scope(field: str) -> None:
    with pytest.raises(ValueError):
        plan(
            source().model_copy(update={field: UUID(int=999)}),
            observation().model_copy(update={field: UUID(int=999)}),
        )


@pytest.mark.parametrize("field,value", [("closed_packs", True), ("closed_packs", -1)])
def test_forged_count_model_is_revalidated(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        plan(source(), observation().model_copy(update={field: value}))


def test_forged_nested_packaging_and_source_are_revalidated() -> None:
    counted = observation()
    forged = counted.packaging.model_copy(update={"units_per_pack": True})
    with pytest.raises(ValueError):
        plan(source(), counted.model_copy(update={"packaging": forged}))
    with pytest.raises(ValueError):
        plan(source().model_copy(update={"legacy_quantity": Decimal("0.0001")}), counted)


def test_maximum_closed_stock_is_an_aggregate_not_one_entry_per_pack() -> None:
    count = MAX_BASE_UNITS // 3
    result = plan(source(str(count)), observation(closed=count, size=3), Decimal("0"), Decimal("0"))
    assert result.total_base_units == count * 3
    assert result.closed_range is not None and result.closed_range.pack_count == count
    assert result.opened_slices == ()
    with pytest.raises(ValidationError):
        observation(closed=count + 1, size=3)


def test_money_overflow_is_rejected_without_truncation() -> None:
    with pytest.raises(ValueError):
        plan(source("2"), observation(closed=2), MAX_MONEY, MAX_MONEY)


def test_plan_is_independent_of_decimal_precision_and_traps() -> None:
    original, counted = source(), observation()
    with localcontext() as context:
        context.prec = 1
        context.traps[Inexact] = context.traps[Rounded] = context.traps[InvalidOperation] = True
        context.flags[InvalidOperation] = context.flags[Rounded] = True
        flags = dict(context.flags)
        result = plan(original, counted, MAX_MONEY, MAX_MONEY)
        assert result.total_cost == MAX_MONEY
        assert result.legacy_quantity_delta == Decimal("-1")
        assert context.flags == flags
