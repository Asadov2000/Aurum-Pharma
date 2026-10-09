"""Pure return planning preserves original physical and discounted money facts."""

from dataclasses import FrozenInstanceError
from decimal import Decimal, Inexact, Rounded, localcontext
from typing import cast
from uuid import UUID

import pytest

from app.domains.inventory.unit_stock_v2 import SaleFormatV2, UnitPackagingV2
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.physical_return_allocation import (
    PhysicalReturnSelection,
    plan_physical_return,
)

LINE_ID = UUID(int=20)


def checkout(
    *,
    units: int = 30,
    net: str = "10.01",
    discount: str = "1.00",
    step: int = 1,
    blister: int | None = 10,
) -> PhysicalCheckoutResultV3:
    gross = Decimal(net) + Decimal(discount)
    scope = {
        field: UUID(int=index)
        for index, field in enumerate(
            (
                "tenant_id",
                "branch_id",
                "register_id",
                "shift_id",
                "cashier_id",
                "event_id",
                "sale_id",
                "operation_id",
                "stock_command_id",
            ),
            start=1,
        )
    }
    return PhysicalCheckoutResultV3.model_validate(
        {
            **scope,
            "schema_version": 3,
            "created_at": "2026-10-07T10:00:00Z",
            "completed_at": "2026-10-07T10:01:00Z",
            "receipt_number": "A-42",
            "receipt_sequence": 42,
            "is_test": True,
            "lines": [
                {
                    "id": LINE_ID,
                    "position": 1,
                    "catalog_id": UUID(int=21),
                    "name": "Лекарство 500 мг",
                    "packaging": {
                        "version": 2,
                        "unit": "tablet",
                        "units_per_pack": units,
                        "units_per_blister": blister,
                        "dispense_step": step,
                    },
                    "sale_format": "pack",
                    "quantity": 1,
                    "base_units": units,
                    "original_retail_unit_price": gross,
                    "gross_amount": gross,
                    "discount_amount": Decimal(discount),
                    "net_amount": Decimal(net),
                }
            ],
            "payments": [{"id": UUID(int=22), "method": "cash", "amount": net}],
            "total_amount": net,
        }
    )


def selection(
    quantity: int = 1,
    sale_format: SaleFormatV2 = "unit",
    line_id: UUID = LINE_ID,
) -> PhysicalReturnSelection:
    return PhysicalReturnSelection(line_id=line_id, sale_format=sale_format, quantity=quantity)


def test_partial_pack_can_return_units_then_blister_using_base_unit_cursor() -> None:
    original = checkout(net="10.01", discount="1.01")
    first = plan_physical_return(original, (selection(2),), returned_base_units={})
    second = plan_physical_return(
        original, (selection(1, "blister"),), returned_base_units={LINE_ID: 2}
    )
    assert first.lines[0].base_units == 2
    assert first.lines[0].offset == 0
    assert first.lines[0].net_amount == Decimal("0.66")
    assert first.lines[0].discount_amount == Decimal("0.06")
    assert first.lines[0].gross_amount == Decimal("0.72")
    assert second.lines[0].base_units == 10
    assert second.lines[0].offset == 2
    assert second.lines[0].net_amount == Decimal("3.34")
    assert second.lines[0].discount_amount == Decimal("0.34")
    assert second.lines[0].gross_amount == Decimal("3.68")
    assert second.lines[0].original_net_amount == original.lines[0].net_amount
    assert second.total_amount == second.lines[0].net_amount


def test_plan_carries_original_scope_and_is_immutable() -> None:
    original = checkout()
    chosen = selection(1, "pack")
    result = plan_physical_return(original, (chosen,), returned_base_units={})
    assert result.version == 1
    assert result.currency == "TJS"
    assert result.tenant_id == original.tenant_id
    assert result.branch_id == original.branch_id
    assert result.sale_id == original.sale_id
    assert result.parent_operation_id == original.operation_id
    assert result.lines[0].line_id == LINE_ID
    assert result.lines[0].sale_format == "pack"
    assert result.lines[0].quantity == 1
    assert result.total_amount == original.total_amount
    for target, attribute, value in (
        (result, "total_amount", Decimal("0.00")),
        (result.lines[0], "quantity", 2),
        (chosen, "quantity", 2),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(target, attribute, value)


def test_catalog_changes_do_not_replace_historical_packaging() -> None:
    historical = checkout()
    current_catalog = UnitPackagingV2(
        unit="tablet", units_per_pack=60, units_per_blister=20, dispense_step=2
    )
    plan = plan_physical_return(historical, (selection(1, "blister"),), returned_base_units={})
    assert current_catalog.units_per_blister == 20
    assert plan.lines[0].base_units == 10
    assert historical.lines[0].packaging.units_per_pack == 30


@pytest.mark.parametrize("units", [3, 7, 30])
def test_adjacent_returns_sum_to_original_net_discount_and_gross(units: int) -> None:
    original = checkout(units=units, blister=None, net="0.02", discount="0.01")
    allocations = tuple(
        plan_physical_return(original, (selection(),), returned_base_units={LINE_ID: offset}).lines[
            0
        ]
        for offset in range(units)
    )
    assert sum((line.net_amount for line in allocations), Decimal("0.00")) == Decimal("0.02")
    assert sum((line.discount_amount for line in allocations), Decimal("0.00")) == Decimal("0.01")
    assert sum((line.gross_amount for line in allocations), Decimal("0.00")) == Decimal("0.03")
    assert allocations[-1].net_amount == Decimal("0.01")
    assert allocations[-1].discount_amount == Decimal("0.01")
    assert all(line.gross_amount == line.net_amount + line.discount_amount for line in allocations)


def test_positive_physical_quantity_may_allocate_zero_money() -> None:
    result = plan_physical_return(
        checkout(net="0.01", discount="0.00"), (selection(),), returned_base_units={}
    )
    assert result.lines[0].base_units == 1
    assert result.lines[0].net_amount == Decimal("0.00")
    assert result.lines[0].discount_amount == Decimal("0.00")
    assert result.lines[0].gross_amount == Decimal("0.00")
    assert result.total_amount == Decimal("0.00")


@pytest.mark.parametrize("quantity", [True, False, 1.0, "1", 0, -1, 31])
def test_invalid_quantity_is_rejected(quantity: object) -> None:
    with pytest.raises(ValueError):
        plan_physical_return(checkout(), (selection(cast(int, quantity)),), returned_base_units={})


@pytest.mark.parametrize("cursor", [True, False, 1.0, "1", -1, 30, 31])
def test_invalid_or_exhausted_cursor_is_rejected(cursor: object) -> None:
    with pytest.raises(ValueError):
        plan_physical_return(
            checkout(), (selection(),), returned_base_units={LINE_ID: cast(int, cursor)}
        )


def test_return_cannot_exceed_remaining_units() -> None:
    with pytest.raises(ValueError):
        plan_physical_return(
            checkout(), (selection(1, "blister"),), returned_base_units={LINE_ID: 21}
        )


def test_unknown_selection_and_duplicate_selection_are_rejected() -> None:
    for selections in ((selection(line_id=UUID(int=999)),), (selection(), selection())):
        with pytest.raises(ValueError):
            plan_physical_return(checkout(), selections, returned_base_units={})


def test_unknown_cursor_is_rejected_even_when_not_selected() -> None:
    with pytest.raises(ValueError):
        plan_physical_return(checkout(), (selection(),), returned_base_units={UUID(int=999): 0})


@pytest.mark.parametrize("count", [0, 201])
def test_selection_count_is_bounded(count: int) -> None:
    with pytest.raises(ValueError):
        plan_physical_return(
            checkout(), tuple(selection() for _ in range(count)), returned_base_units={}
        )


def test_unknown_blister_composition_and_historical_step_are_respected() -> None:
    with pytest.raises(ValueError):
        plan_physical_return(
            checkout(blister=None), (selection(1, "blister"),), returned_base_units={}
        )
    with pytest.raises(ValueError):
        plan_physical_return(checkout(step=2), (selection(),), returned_base_units={})


def test_unknown_sale_format_is_rejected() -> None:
    with pytest.raises(ValueError):
        plan_physical_return(
            checkout(),
            (selection(sale_format=cast(SaleFormatV2, "legacy_pack")),),
            returned_base_units={},
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"base_units": 29},
        {"quantity": True},
        {"net_amount": Decimal("NaN")},
        {"discount_amount": Decimal("0.001")},
        {"gross_amount": Decimal("11.00")},
        {"money_version": 2},
    ],
)
def test_unvalidated_line_copy_is_revalidated(changes: dict[str, object]) -> None:
    original = checkout()
    invalid = original.model_copy(update={"lines": (original.lines[0].model_copy(update=changes),)})
    with pytest.raises(ValueError):
        plan_physical_return(invalid, (selection(),), returned_base_units={})


@pytest.mark.parametrize(
    "changes",
    [
        {"currency": "USD"},
        {"schema_version": 2},
        {"total_amount": Decimal("10.00")},
        {"lines": ()},
    ],
)
def test_unvalidated_checkout_copy_is_revalidated(changes: dict[str, object]) -> None:
    invalid = checkout().model_copy(update=changes)
    with pytest.raises(ValueError):
        plan_physical_return(invalid, (selection(),), returned_base_units={})


def test_constructed_invalid_packaging_is_revalidated() -> None:
    original = checkout()
    invalid_packaging = original.lines[0].packaging.model_copy(update={"units_per_blister": 7})
    invalid = original.model_copy(
        update={"lines": (original.lines[0].model_copy(update={"packaging": invalid_packaging}),)}
    )
    with pytest.raises(ValueError):
        plan_physical_return(invalid, (selection(),), returned_base_units={})


def test_multiple_lines_sum_selected_amounts_and_accept_exhausted_unselected_line() -> None:
    original = checkout()
    payload = original.model_dump(mode="python")
    second_id = UUID(int=30)
    second = original.lines[0].model_dump(mode="python")
    second.update(id=second_id, position=2)
    payload.update(
        lines=(original.lines[0].model_dump(mode="python"), second),
        payments=({"id": UUID(int=22), "method": "cash", "amount": "20.02"},),
        total_amount="20.02",
    )
    original = PhysicalCheckoutResultV3.model_validate(payload)
    result = plan_physical_return(
        original,
        (selection(1, "blister"), selection(1, "pack", second_id)),
        returned_base_units={},
    )
    assert {line.line_id for line in result.lines} == {LINE_ID, second_id}
    assert result.total_amount == Decimal("13.34")
    partial = plan_physical_return(
        original,
        (selection(),),
        returned_base_units={LINE_ID: 0, second_id: 30},
    )
    assert len(partial.lines) == 1
    assert partial.lines[0].line_id == LINE_ID


def test_mutable_cursor_input_and_checkout_are_not_modified() -> None:
    original = checkout()
    before = original.model_dump(mode="python")
    cursors = {LINE_ID: 2}
    result = plan_physical_return(original, (selection(2),), returned_base_units=cursors)
    assert cursors == {LINE_ID: 2}
    assert original.model_dump(mode="python") == before
    cursors[LINE_ID] = 29
    assert result.lines[0].offset == 2


def test_hostile_decimal_context_does_not_change_allocated_money() -> None:
    original = checkout(net="999999999998.99", discount="1.00")
    expected = plan_physical_return(
        original, (selection(1, "blister"),), returned_base_units={LINE_ID: 2}
    )
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        actual = plan_physical_return(
            original, (selection(1, "blister"),), returned_base_units={LINE_ID: 2}
        )
    assert actual == expected
