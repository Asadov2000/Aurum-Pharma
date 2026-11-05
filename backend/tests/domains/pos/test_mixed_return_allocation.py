"""Mixed return plans preserve saved money and both inventory quantity semantics."""

from dataclasses import FrozenInstanceError
from decimal import Decimal, Inexact, Rounded, localcontext
from typing import cast
from uuid import UUID

import pytest

from app.domains.catalog.unit_sales import MAX_BASE_UNITS
from app.domains.inventory.unit_stock_v2 import SaleFormatV2
from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.mixed_return_allocation import (
    LegacyReturnAllocation,
    LegacyReturnSelection,
    MixedPhysicalReturnAllocation,
    MixedReturnPlan,
    plan_mixed_return,
)
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.unit_contracts import EXACT_CONTEXT
from tests.domains.pos.test_mixed_documents import legacy, line, mixed_payload
from tests.domains.pos.test_physical_documents import packaging

LEGACY_ID = UUID(int=40)
PHYSICAL_ID = UUID(int=20)
FOREIGN_ID = UUID(int=999)
Selection = LegacyReturnSelection | PhysicalReturnSelection


def checkout(
    *,
    legacy_quantity: str = "3.000",
    legacy_price: str = "4.00",
    legacy_discount: str = "1.99",
    physical_units: int = 30,
    physical_net: str = "10.01",
    physical_discount: str = "1.01",
    step: int = 1,
    blister: int | None = 10,
) -> MixedCheckoutResultV4:
    with localcontext(EXACT_CONTEXT):
        gross = (Decimal(legacy_quantity) * Decimal(legacy_price)).quantize(Decimal("0.01"))
        legacy_net = gross - Decimal(legacy_discount)
        physical_gross = Decimal(physical_net) + Decimal(physical_discount)
        total = legacy_net + Decimal(physical_net)
    return MixedCheckoutResultV4.model_validate(
        mixed_payload(
            lines=[
                legacy(
                    quantity=legacy_quantity,
                    original_retail_unit_price=legacy_price,
                    gross_amount=gross,
                    discount_amount=legacy_discount,
                    net_amount=legacy_net,
                ),
                line(
                    id=PHYSICAL_ID,
                    position=2,
                    catalog_id=UUID(int=21),
                    packaging=packaging(
                        units_per_pack=physical_units,
                        units_per_blister=blister,
                        dispense_step=step,
                    ),
                    sale_format="pack",
                    quantity=1,
                    base_units=physical_units,
                    original_retail_unit_price=physical_gross,
                    gross_amount=physical_gross,
                    discount_amount=physical_discount,
                    net_amount=physical_net,
                ),
            ],
            payments=[{"id": UUID(int=22), "method": "cash", "amount": total}],
            total_amount=total,
        )
    )


def legacy_selection(quantity: str = "1.000", line_id: UUID = LEGACY_ID) -> LegacyReturnSelection:
    return LegacyReturnSelection(line_id=line_id, quantity=Decimal(quantity))


def physical_selection(
    quantity: int = 1,
    sale_format: SaleFormatV2 = "unit",
    line_id: UUID = PHYSICAL_ID,
) -> PhysicalReturnSelection:
    return PhysicalReturnSelection(line_id=line_id, quantity=quantity, sale_format=sale_format)


def plan(
    original: MixedCheckoutResultV4,
    selections: tuple[Selection, ...],
    *,
    legacy_cursors: dict[UUID, Decimal] | None = None,
    physical_cursors: dict[UUID, int] | None = None,
) -> MixedReturnPlan:
    return plan_mixed_return(
        original,
        selections,
        returned_legacy_quantities=legacy_cursors if legacy_cursors is not None else {},
        returned_base_units=physical_cursors if physical_cursors is not None else {},
    )


def test_both_kinds_are_sorted_by_original_position_and_preserve_scope() -> None:
    original = checkout()
    result = plan(original, (physical_selection(1, "pack"), legacy_selection("3.000")))
    assert result.version == 1
    assert result.currency == "TJS"
    assert result.tenant_id == original.tenant_id
    assert result.branch_id == original.branch_id
    assert result.sale_id == original.sale_id
    assert result.parent_operation_id == original.operation_id
    assert isinstance(result.lines, tuple)
    assert [(row.line_id, row.position, row.inventory_kind) for row in result.lines] == [
        (LEGACY_ID, 1, "legacy_pack"),
        (PHYSICAL_ID, 2, "physical_unit"),
    ]
    legacy_row, physical_row = result.lines
    assert isinstance(legacy_row, LegacyReturnAllocation)
    assert isinstance(physical_row, MixedPhysicalReturnAllocation)
    assert legacy_row.quantity == legacy_row.original_quantity == Decimal("3.000")
    assert legacy_row.offset == Decimal("0.000")
    assert physical_row.sale_format == "pack"
    assert physical_row.quantity == 1
    assert physical_row.base_units == physical_row.original_base_units == 30
    assert physical_row.offset == 0
    assert result.total_amount == original.total_amount == Decimal("20.02")
    for returned, source in zip(result.lines, original.lines, strict=True):
        assert returned.original_net_amount == returned.net_amount == source.net_amount
        assert returned.discount_amount == source.discount_amount
        assert returned.gross_amount == source.gross_amount


@pytest.mark.parametrize("kind", ["legacy", "physical"])
def test_one_kind_may_be_returned_without_the_other(kind: str) -> None:
    chosen: Selection = legacy_selection("3.000") if kind == "legacy" else physical_selection(30)
    result = plan(checkout(), (chosen,))
    assert len(result.lines) == 1
    assert result.lines[0].line_id == chosen.line_id
    assert result.total_amount == Decimal("10.01")


def test_partial_fractional_packs_and_physical_blisters_use_separate_cursors() -> None:
    result = plan(
        checkout(),
        (physical_selection(1, "blister"), legacy_selection("0.125")),
        legacy_cursors={LEGACY_ID: Decimal("0.001")},
        physical_cursors={PHYSICAL_ID: 2},
    )
    legacy_row, physical_row = result.lines
    assert isinstance(legacy_row, LegacyReturnAllocation)
    assert legacy_row.offset == Decimal("0.001")
    assert legacy_row.quantity == Decimal("0.125")
    assert legacy_row.net_amount == Decimal("0.42")
    assert legacy_row.discount_amount == Decimal("0.08")
    assert legacy_row.gross_amount == Decimal("0.50")
    assert isinstance(physical_row, MixedPhysicalReturnAllocation)
    assert physical_row.base_units == 10
    assert physical_row.offset == 2
    assert physical_row.net_amount == Decimal("3.34")
    assert physical_row.discount_amount == Decimal("0.34")
    assert physical_row.gross_amount == Decimal("3.68")
    assert result.total_amount == Decimal("3.76")


@pytest.mark.parametrize("parts", [3, 7, 30])
def test_adjacent_parts_preserve_every_saved_cent_for_both_kinds(parts: int) -> None:
    original = checkout(
        legacy_quantity=str(parts),
        legacy_price="0.01",
        legacy_discount=str(Decimal(parts) / 100 - Decimal("0.02")),
        physical_units=parts,
        physical_net="0.02",
        physical_discount="0.01",
        blister=None,
    )
    results = tuple(
        plan(
            original,
            (physical_selection(), legacy_selection()),
            legacy_cursors={LEGACY_ID: Decimal(offset)},
            physical_cursors={PHYSICAL_ID: offset},
        )
        for offset in range(parts)
    )
    for position, source in enumerate(original.lines):
        for attribute in ("net_amount", "discount_amount", "gross_amount"):
            assert sum(
                (getattr(result.lines[position], attribute) for result in results),
                Decimal("0.00"),
            ) == getattr(source, attribute)
    assert results[-1].lines[0].net_amount == Decimal("0.01")
    assert results[-1].lines[1].net_amount == Decimal("0.01")
    total_returned = sum((result.total_amount for result in results), Decimal("0.00"))
    assert total_returned == original.total_amount


def test_positive_millipack_can_allocate_zero_net_discount_and_gross() -> None:
    original = checkout(legacy_discount="11.99", physical_net="0.01", physical_discount="0.00")
    result = plan(original, (legacy_selection("0.001"), physical_selection()))
    assert result.total_amount == Decimal("0.00")
    assert result.lines[0].net_amount == Decimal("0.00")
    assert result.lines[0].discount_amount == Decimal("0.00")
    assert result.lines[0].gross_amount == Decimal("0.00")
    assert result.lines[1].net_amount == Decimal("0.00")


def test_fully_discounted_legacy_return_preserves_discount_and_zero_cash_total() -> None:
    original = checkout(legacy_discount="12.00", physical_net="0.01", physical_discount="0.00")
    result = plan(original, (legacy_selection("3.000"), physical_selection()))
    assert result.total_amount == Decimal("0.00")
    assert result.lines[0].original_net_amount == Decimal("0.00")
    assert result.lines[0].net_amount == Decimal("0.00")
    assert result.lines[0].discount_amount == result.lines[0].gross_amount == Decimal("12.00")
    assert result.lines[1].net_amount == Decimal("0.00")


def test_adjacent_millipacks_preserve_saved_cents() -> None:
    original = checkout(legacy_quantity="0.003", legacy_price="10.00", legacy_discount="0.01")
    results = tuple(
        plan(
            original,
            (legacy_selection("0.001"),),
            legacy_cursors={LEGACY_ID: Decimal(offset) / 1000},
        )
        for offset in range(3)
    )
    assert tuple(result.total_amount for result in results) == (
        Decimal("0.00"),
        Decimal("0.01"),
        Decimal("0.01"),
    )
    assert sum((result.lines[0].discount_amount for result in results), Decimal("0.00")) == Decimal(
        "0.01"
    )


def test_maximum_legacy_quantity_is_not_restricted_by_physical_unit_limit() -> None:
    original = checkout(
        legacy_quantity="99999999999.999", legacy_price="0.01", legacy_discount="0.00"
    )
    assert int(Decimal("99999999999.999") * 1000) > MAX_BASE_UNITS
    result = plan(original, (legacy_selection("99999999999.999"),))
    assert result.lines[0].net_amount == original.lines[0].net_amount == Decimal("1000000000.00")
    tail = plan(
        original,
        (legacy_selection("0.001"),),
        legacy_cursors={LEGACY_ID: Decimal("99999999999.998")},
    )
    assert tail.lines[0].net_amount == Decimal("0.01")


@pytest.mark.parametrize(
    "quantity",
    [
        True,
        False,
        1,
        1.0,
        "1.000",
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("0.0001"),
        Decimal("0"),
        Decimal("-0.001"),
        Decimal("3.001"),
    ],
)
def test_legacy_quantity_requires_positive_exact_decimal_millipacks(quantity: object) -> None:
    selected = LegacyReturnSelection(line_id=LEGACY_ID, quantity=cast(Decimal, quantity))
    with pytest.raises(ValueError):
        plan(checkout(), (selected,))


@pytest.mark.parametrize("quantity", [True, False, 1.0, "1", Decimal("1"), 0, -1, 31])
def test_physical_quantity_requires_positive_integer_units(quantity: object) -> None:
    with pytest.raises(ValueError):
        plan(checkout(), (physical_selection(cast(int, quantity)),))


@pytest.mark.parametrize(
    "cursor",
    [
        True,
        False,
        0,
        0.0,
        "0.000",
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("0.0001"),
        Decimal("-0.001"),
        Decimal("3.001"),
    ],
)
def test_invalid_legacy_cursor_is_rejected_even_for_unselected_line(cursor: object) -> None:
    with pytest.raises(ValueError):
        plan(checkout(), (physical_selection(),), legacy_cursors={LEGACY_ID: cast(Decimal, cursor)})


@pytest.mark.parametrize("cursor", [True, False, 0.0, "0", Decimal("0"), -1, 31])
def test_invalid_physical_cursor_is_rejected_even_for_unselected_line(cursor: object) -> None:
    with pytest.raises(ValueError):
        plan(checkout(), (legacy_selection(),), physical_cursors={PHYSICAL_ID: cast(int, cursor)})


@pytest.mark.parametrize(
    "legacy_cursor,key",
    [
        (True, FOREIGN_ID),
        (True, PHYSICAL_ID),
        (True, str(LEGACY_ID)),
        (True, True),
        (False, FOREIGN_ID),
        (False, LEGACY_ID),
        (False, str(PHYSICAL_ID)),
        (False, True),
    ],
)
def test_cursor_identity_and_inventory_kind_are_strict(legacy_cursor: bool, key: object) -> None:
    with pytest.raises(ValueError):
        plan(
            checkout(),
            (legacy_selection(), physical_selection()),
            legacy_cursors={cast(UUID, key): Decimal("0")} if legacy_cursor else {},
            physical_cursors={} if legacy_cursor else {cast(UUID, key): 0},
        )


def test_exhausted_unselected_lines_are_valid_but_selected_remaining_range_is_enforced() -> None:
    original = checkout()
    legacy_exhausted = plan(
        original, (physical_selection(),), legacy_cursors={LEGACY_ID: Decimal("3")}
    )
    assert len(legacy_exhausted.lines) == 1
    assert len(plan(original, (legacy_selection(),), physical_cursors={PHYSICAL_ID: 30}).lines) == 1
    for chosen, legacy_cursors, physical_cursors in (
        (legacy_selection("0.002"), {LEGACY_ID: Decimal("2.999")}, {}),
        (legacy_selection("0.001"), {LEGACY_ID: Decimal("3")}, {}),
        (physical_selection(1, "blister"), {}, {PHYSICAL_ID: 21}),
        (physical_selection(), {}, {PHYSICAL_ID: 30}),
    ):
        with pytest.raises(ValueError):
            plan(
                original,
                (chosen,),
                legacy_cursors=legacy_cursors,
                physical_cursors=physical_cursors,
            )


@pytest.mark.parametrize(
    "selections",
    [
        (legacy_selection(line_id=FOREIGN_ID),),
        (physical_selection(line_id=FOREIGN_ID),),
        (legacy_selection(line_id=PHYSICAL_ID),),
        (physical_selection(line_id=LEGACY_ID),),
        (legacy_selection(), legacy_selection()),
        (physical_selection(), physical_selection()),
        (LegacyReturnSelection(cast(UUID, str(LEGACY_ID)), Decimal("1")),),
        (PhysicalReturnSelection(cast(UUID, str(PHYSICAL_ID)), "unit", 1),),
        (LegacyReturnSelection(cast(UUID, True), Decimal("1")),),
        (PhysicalReturnSelection(cast(UUID, True), "unit", 1),),
    ],
)
def test_foreign_wrong_kind_duplicate_and_string_identifiers_are_rejected(
    selections: tuple[Selection, ...],
) -> None:
    with pytest.raises(ValueError):
        plan(checkout(), selections)


@pytest.mark.parametrize(
    "selections",
    [
        (),
        (legacy_selection(),) * 201,
        [legacy_selection()],
        ({"line_id": LEGACY_ID, "quantity": "1"},),
        (object(),),
    ],
)
def test_selection_container_count_and_exact_types_are_required(selections: object) -> None:
    with pytest.raises(ValueError):
        plan(checkout(), cast(tuple[Selection, ...], selections))


def test_physical_saved_composition_dispense_step_and_format_are_enforced() -> None:
    with pytest.raises(ValueError):
        plan(checkout(blister=None), (physical_selection(1, "blister"),))
    with pytest.raises(ValueError):
        plan(checkout(step=2), (physical_selection(),))
    with pytest.raises(ValueError):
        plan(checkout(), (physical_selection(sale_format=cast(SaleFormatV2, "legacy_pack")),))
    valid = plan(checkout(step=2), (physical_selection(2),)).lines[0]
    assert isinstance(valid, MixedPhysicalReturnAllocation)
    assert valid.base_units == 2
    blister = plan(checkout(), (physical_selection(1, "blister"),)).lines[0]
    assert isinstance(blister, MixedPhysicalReturnAllocation)
    assert blister.base_units == 10


def test_unselected_physical_cursor_must_match_historical_dispense_step() -> None:
    with pytest.raises(ValueError):
        plan(checkout(step=2), (legacy_selection(),), physical_cursors={PHYSICAL_ID: 1})


@pytest.mark.parametrize(
    "changes",
    [
        {"currency": "USD"},
        {"schema_version": 3},
        {"total_amount": Decimal("0.01")},
        {"lines": ()},
    ],
)
def test_unvalidated_source_copy_is_revalidated(changes: dict[str, object]) -> None:
    invalid = checkout().model_copy(update=changes)
    with pytest.raises(ValueError):
        plan(invalid, (legacy_selection(),))


@pytest.mark.parametrize(
    "index,changes",
    [
        (0, {"quantity": Decimal("0.0001")}),
        (0, {"gross_amount": Decimal("12.01")}),
        (0, {"net_amount": Decimal("NaN")}),
        (0, {"discount_amount": Decimal("0.001")}),
        (1, {"base_units": 29}),
        (1, {"quantity": True}),
        (1, {"money_version": 2}),
        (1, {"net_amount": Decimal("NaN")}),
    ],
)
def test_unvalidated_selected_or_unselected_line_is_revalidated(
    index: int, changes: dict[str, object]
) -> None:
    original = checkout()
    lines = list(original.lines)
    lines[index] = lines[index].model_copy(update=changes)
    invalid = original.model_copy(update={"lines": tuple(lines)})
    with pytest.raises(ValueError):
        plan(invalid, (legacy_selection(),))


def test_constructed_snapshot_and_forged_nested_packaging_are_revalidated() -> None:
    original = checkout()
    payload = original.model_dump(mode="python")
    payload["currency"] = "USD"
    invalid = MixedCheckoutResultV4.model_construct(**payload)
    with pytest.raises(ValueError):
        plan(invalid, (legacy_selection(),))
    physical = original.lines[1]
    assert physical.inventory_kind == "physical_unit"
    invalid_packaging = physical.packaging.model_copy(update={"units_per_blister": 7})
    forged = original.model_copy(
        update={
            "lines": (
                original.lines[0],
                physical.model_copy(update={"packaging": invalid_packaging}),
            )
        }
    )
    with pytest.raises(ValueError):
        plan(forged, (legacy_selection(),))


@pytest.mark.parametrize("source", [None, {}, object()])
def test_exact_mixed_source_type_is_required(source: object) -> None:
    with pytest.raises(ValueError):
        plan(cast(MixedCheckoutResultV4, source), (legacy_selection(),))


def test_source_and_selection_subclasses_cannot_bypass_exact_type_requirement() -> None:
    class DerivedCheckout(MixedCheckoutResultV4):
        pass

    class DerivedLegacySelection(LegacyReturnSelection):
        pass

    class DerivedPhysicalSelection(PhysicalReturnSelection):
        pass

    derived_source = DerivedCheckout.model_validate(checkout().model_dump(mode="python"))
    with pytest.raises(ValueError):
        plan(derived_source, (legacy_selection(),))
    with pytest.raises(ValueError):
        plan(checkout(), (DerivedLegacySelection(LEGACY_ID, Decimal("1")),))
    with pytest.raises(ValueError):
        plan(checkout(), (DerivedPhysicalSelection(PHYSICAL_ID, "unit", 1),))


def test_inputs_are_unchanged_and_every_output_and_selection_is_immutable() -> None:
    original = checkout()
    before = original.model_dump(mode="python")
    legacy_cursors = {LEGACY_ID: Decimal("0.001")}
    physical_cursors = {PHYSICAL_ID: 2}
    selected_legacy = legacy_selection()
    selected_physical = physical_selection()
    result = plan(
        original,
        (selected_legacy, selected_physical),
        legacy_cursors=legacy_cursors,
        physical_cursors=physical_cursors,
    )
    assert original.model_dump(mode="python") == before
    assert legacy_cursors == {LEGACY_ID: Decimal("0.001")}
    assert physical_cursors == {PHYSICAL_ID: 2}
    legacy_cursors[LEGACY_ID] = Decimal("2.000")
    physical_cursors[PHYSICAL_ID] = 20
    assert result.lines[0].offset == Decimal("0.001")
    assert result.lines[1].offset == 2
    for target, attribute, value in (
        (result, "total_amount", Decimal("0.00")),
        (result.lines[0], "quantity", Decimal("2")),
        (result.lines[1], "quantity", 2),
        (selected_legacy, "quantity", Decimal("2")),
        (selected_physical, "quantity", 2),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(target, attribute, value)


def test_hostile_decimal_context_does_not_change_mixed_allocation() -> None:
    original = checkout(
        legacy_quantity="99999999999.999", legacy_price="0.01", legacy_discount="1.01"
    )
    chosen = (legacy_selection("1234567.001"), physical_selection(1, "blister"))
    legacy_cursors = {LEGACY_ID: Decimal("9876543.001")}
    physical_cursors = {PHYSICAL_ID: 2}
    expected = plan(
        original, chosen, legacy_cursors=legacy_cursors, physical_cursors=physical_cursors
    )
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        actual = plan(
            original, chosen, legacy_cursors=legacy_cursors, physical_cursors=physical_cursors
        )
    assert actual == expected
