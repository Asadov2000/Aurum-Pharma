"""Native movement proposals reuse real allocation without exposing financial costs."""

import json
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.inventory.unit_stock_v2 import OpenedBlisterV2, OpenedPackV2, SaleFormatV2
from app.domains.pos import edge_physical_cash_plan as plan_module
from app.domains.pos.edge_physical_cash_plan import (
    NativePhysicalCashPlanV1,
    build_edge_physical_cash_plan,
)
from app.domains.pos.edge_retail_cash_draft import EdgeRetailCashDraftV1
from app.domains.pos.unit_cart_quote import PhysicalUnitDraftQuoteLineV2
from tests.domains.pos.test_edge_retail_cash_repricing import _replace_quote, draft_example


def physical_draft(
    rows: tuple[tuple[SaleFormatV2, int, Decimal], ...],
) -> EdgeRetailCashDraftV1:
    _, draft = draft_example()
    item = draft.unit_items[0]
    old_quote = draft.inputs.saved_quote
    original = old_quote.lines[0]
    assert isinstance(original, PhysicalUnitDraftQuoteLineV2)
    items, quoted = [], []
    for position, (sale_format, quantity, discount) in enumerate(rows, 1):
        line_id = uuid4()
        price = {"pack": Decimal("30.00"), "blister": Decimal("12.50"), "unit": Decimal("1.25")}
        units = {"pack": 30, "blister": 10, "unit": 1}
        items.append(
            item.model_copy(
                update={
                    "id": line_id,
                    "position": position,
                    "sale_format": sale_format,
                    "quantity": quantity,
                    "discount_amount": discount,
                }
            )
        )
        gross = price[sale_format] * quantity
        quoted.append(
            original.model_copy(
                update={
                    "id": line_id,
                    "position": position,
                    "sale_format": sale_format,
                    "quantity": quantity,
                    "base_units": units[sale_format] * quantity,
                    "original_retail_unit_price": price[sale_format],
                    "gross_amount": gross,
                    "discount_amount": discount,
                    "net_amount": gross - discount,
                }
            )
        )
    quote = old_quote.model_copy(
        update={
            "lines": tuple(quoted),
            "total_amount": sum((line.net_amount for line in quoted), Decimal("0.00")),
        }
    )
    return _replace_quote(draft.model_copy(update={"unit_items": tuple(items)}), quote)


@pytest.mark.parametrize(
    "sale_format,kinds,units",
    [
        ("pack", ["pack_take"], 30),
        ("blister", ["pack_open", "blister_take"], 10),
        ("unit", ["pack_open", "unit_take"], 1),
    ],
)
def test_three_formats_propose_exact_movements_without_costs_or_sealed_ordinals(
    sale_format: SaleFormatV2, kinds: list[str], units: int
) -> None:
    draft = physical_draft(((sale_format, 1, Decimal("0.00")),))
    before = draft.model_dump(mode="json")
    plan = build_edge_physical_cash_plan(draft)
    assert plan.quote == draft.inputs.saved_quote
    assert [movement.kind for movement in plan.movements] == kinds
    assert sum(movement.units_taken for movement in plan.movements) == units
    assert all(movement.id.version == 4 for movement in plan.movements)
    assert all(
        set(movement.model_dump())
        == {
            "id",
            "line_id",
            "lot_id",
            "pack_id",
            "kind",
            "entry_order",
            "closed_delta",
            "units_taken",
            "before_state",
            "after_state",
        }
        for movement in plan.movements
    )
    assert draft.model_dump(mode="json") == before


def test_new_pack_alias_is_opened_once_and_reused_across_format_allocations() -> None:
    draft = physical_draft(
        (
            ("unit", 2, Decimal("0.00")),
            ("pack", 1, Decimal("0.00")),
            ("blister", 1, Decimal("0.00")),
        )
    )
    plan = build_edge_physical_cash_plan(draft)
    assert [movement.kind for movement in plan.movements] == [
        "pack_take",
        "pack_open",
        "blister_take",
        "unit_take",
    ]
    opened = plan.movements[1]
    assert opened.pack_id is not None and opened.pack_id.version == 5
    assert all(movement.pack_id == opened.pack_id for movement in plan.movements[1:])
    assert [movement.entry_order for movement in plan.movements] == [0, 1, 2, 3]
    assert sum(movement.units_taken for movement in plan.movements) == 42
    assert [movement.line_id for movement in plan.movements] == [
        draft.unit_items[1].id,
        draft.unit_items[2].id,
        draft.unit_items[2].id,
        draft.unit_items[0].id,
    ]


def test_existing_open_fragment_preserves_actual_pack_identity_and_history() -> None:
    draft = physical_draft((("unit", 2, Decimal("0.00")),))
    lot = draft.inputs.physical_stock[0]
    pack = OpenedPackV2(
        id=uuid4(),
        opened_at=lot.created_at,
        intact_blisters=(2, 3),
        opened_blisters=(OpenedBlisterV2(number=1, remaining_units=4),),
    )
    inputs = draft.inputs.model_copy(
        update={"physical_stock": (lot.model_copy(update={"opened_packs": (pack,)}),)}
    )
    plan = build_edge_physical_cash_plan(draft.model_copy(update={"inputs": inputs}))
    assert len(plan.movements) == 1
    movement = plan.movements[0]
    assert movement.pack_id == pack.id and movement.kind == "unit_take"
    assert movement.closed_delta == 0
    assert movement.before_state is not None and movement.after_state is not None
    assert movement.before_state.opened_blisters[0].remaining_units == 4
    assert movement.after_state.opened_blisters[0].remaining_units == 2


def test_unknown_blister_composition_uses_existing_loose_units_without_opening() -> None:
    draft = physical_draft((("unit", 2, Decimal("0.00")),))
    lot = draft.inputs.physical_stock[0]
    packaging = lot.packaging.model_copy(update={"units_per_blister": None})
    pack = OpenedPackV2(id=uuid4(), opened_at=lot.created_at, loose_units=3)
    item = draft.unit_items[0].model_copy(update={"physical_packaging": packaging})
    catalog = draft.inputs.catalogs[0]
    assert catalog.unit_sale_preparation is not None
    inputs = draft.inputs.model_copy(
        update={
            "physical_stock": (
                lot.model_copy(update={"packaging": packaging, "opened_packs": (pack,)}),
            ),
            "catalogs": (
                catalog.model_copy(
                    update={
                        "unit_sale_preparation": catalog.unit_sale_preparation.model_copy(
                            update={"units_per_blister": None}
                        )
                    }
                ),
            ),
        }
    )
    quote = draft.inputs.saved_quote.model_copy(
        update={
            "lines": (
                draft.inputs.saved_quote.lines[0].model_copy(update={"packaging": packaging}),
            )
        }
    )
    changed = _replace_quote(
        draft.model_copy(update={"inputs": inputs, "unit_items": (item,)}), quote
    )
    plan = build_edge_physical_cash_plan(changed)
    assert len(plan.movements) == 1
    movement = plan.movements[0]
    assert movement.kind == "unit_take" and movement.pack_id == pack.id
    assert movement.before_state is not None and movement.after_state is not None
    assert movement.before_state.loose_units == 3 and movement.after_state.loose_units == 1


def test_stock_fingerprint_keeps_discount_in_the_existing_retail_contract() -> None:
    _, draft = draft_example()
    original = build_edge_physical_cash_plan(draft)
    item = draft.unit_items[0].model_copy(update={"discount_amount": Decimal("0.10")})
    line = draft.inputs.saved_quote.lines[0].model_copy(
        update={"discount_amount": Decimal("0.10"), "net_amount": Decimal("2.40")}
    )
    quote = draft.inputs.saved_quote.model_copy(
        update={"lines": (line,), "total_amount": Decimal("2.40")}
    )
    changed = _replace_quote(draft.model_copy(update={"unit_items": (item,)}), quote)
    assert build_edge_physical_cash_plan(changed).stock_request_hash != original.stock_request_hash


def test_fefo_oracle_excludes_blocked_stock_and_chooses_earliest_eligible_lot() -> None:
    _, draft = draft_example()
    lot = draft.inputs.physical_stock[0]
    early = lot.model_copy(update={"id": uuid4(), "expires_at": lot.expires_at - timedelta(days=2)})
    blocked = lot.model_copy(
        update={"id": uuid4(), "expires_at": lot.expires_at - timedelta(days=3), "is_blocked": True}
    )
    inputs = draft.inputs.model_copy(
        update={"physical_stock": tuple(sorted((lot, early, blocked), key=lambda row: str(row.id)))}
    )
    plan = build_edge_physical_cash_plan(draft.model_copy(update={"inputs": inputs}))
    assert {movement.lot_id for movement in plan.movements} == {early.id}


def test_current_draft_is_recomputed_instead_of_accepting_a_copied_saved_quote() -> None:
    _, draft = draft_example()
    forged = draft.model_copy(
        update={"unit_items": (draft.unit_items[0].model_copy(update={"quantity": 3}),)}
    )
    with pytest.raises(ConflictError):
        build_edge_physical_cash_plan(forged)


def test_mixed_checkout_is_not_silently_reduced_to_its_physical_positions() -> None:
    _, draft = draft_example("mixed_cash")
    with pytest.raises(BusinessRuleError, match="mixed"):
        build_edge_physical_cash_plan(draft)


def test_constructed_nested_row_cannot_bypass_integer_validation() -> None:
    _, draft = draft_example()
    forged = draft.model_copy(
        update={"unit_items": (draft.unit_items[0].model_copy(update={"quantity": True}),)}
    )
    with pytest.raises(ValidationError):
        build_edge_physical_cash_plan(forged)


@pytest.mark.parametrize("field", ["sealed_start", "sealed_count", "cost_amount", "before_cost"])
def test_server_owned_ordinals_and_cost_fields_are_rejected(field: str) -> None:
    _, draft = draft_example()
    raw = build_edge_physical_cash_plan(draft).model_dump(mode="json")
    raw["movements"][0][field] = 1
    with pytest.raises(ValidationError):
        NativePhysicalCashPlanV1.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize(
    "change", ["duplicate", "gap", "unknown_line", "wrong_units", "bool_units"]
)
def test_wire_contract_rejects_incomplete_or_rebound_movement_oracles(change: str) -> None:
    _, draft = draft_example()
    raw = build_edge_physical_cash_plan(draft).model_dump(mode="json")
    first, take = raw["movements"]
    if change == "duplicate":
        take["id"] = first["id"]
    elif change == "gap":
        take["entry_order"] = 2
    elif change == "unknown_line":
        take["line_id"] = str(uuid4())
    elif change == "wrong_units":
        take["units_taken"] = 3
    else:
        take["units_taken"] = True
    with pytest.raises(ValidationError):
        NativePhysicalCashPlanV1.model_validate_json(json.dumps(raw))


def test_movement_limit_and_encoded_wire_bound_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _, draft = draft_example()
    monkeypatch.setattr(plan_module, "MAX_NATIVE_MOVEMENTS", 1)
    with pytest.raises(BusinessRuleError, match="limit"):
        build_edge_physical_cash_plan(draft)
    monkeypatch.undo()
    monkeypatch.setattr(plan_module, "MAX_INPUT_BYTES", 1)
    with pytest.raises(BusinessRuleError, match="wire bound"):
        build_edge_physical_cash_plan(draft)
