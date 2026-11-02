"""Mixed native proposals retain decimal legacy facts without claiming SQL authority."""

import json
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, ROUND_UP, Decimal, localcontext
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.inventory.unit_stock_v2 import OpenedBlisterV2, OpenedPackV2, SaleFormatV2
from app.domains.pos import edge_physical_cash_plan as physical_module
from app.domains.pos.edge_mixed_cash_plan import NativeMixedCashPlanV1, build_edge_mixed_cash_plan
from app.domains.pos.edge_physical_cash_plan import NativePhysicalCashPlanV1
from app.domains.pos.edge_retail_cash_draft import EdgeRetailCashDraftV1
from app.domains.pos.unit_contracts import LegacyResultLineV2
from tests.domains.pos.test_edge_physical_cash_plan import physical_draft
from tests.domains.pos.test_edge_retail_cash_repricing import _replace_quote, draft_example


def mixed_draft(
    rows: tuple[tuple[SaleFormatV2, int, Decimal], ...] = (("unit", 2, Decimal("0.00")),),
    *,
    legacy_price: Decimal = Decimal("25.00"),
) -> EdgeRetailCashDraftV1:
    draft = physical_draft(rows)
    _, mixed = draft_example("mixed_cash")
    scope = draft.inputs.preparation.scope
    legacy_item = mixed.legacy_items[0]
    original = mixed.inputs.saved_quote.lines[0]
    assert isinstance(original, LegacyResultLineV2)
    # Build authoritative-looking input facts, not a completed result/anchor.
    with localcontext() as context:
        context.rounding = ROUND_HALF_EVEN
        gross = (legacy_price * Decimal("0.001")).quantize(Decimal("0.01"))
    legacy = legacy_item.model_copy(
        update={
            "tenant_id": scope.tenant_id,
            "sale_id": draft.inputs.saved_quote.sale_id,
            "quantity": Decimal("0.001"),
            "unit_price": legacy_price,
            "total_price": gross,
        }
    )
    catalog = next(row for row in mixed.inputs.catalogs if row.id == legacy.catalog_id)
    inputs = draft.inputs.model_copy(
        update={
            "preparation": draft.inputs.preparation.model_copy(update={"kind": "mixed_cash"}),
            "catalogs": tuple(
                sorted(
                    (
                        *draft.inputs.catalogs,
                        catalog.model_copy(update={"tenant_id": scope.tenant_id}),
                    ),
                    key=lambda row: str(row.id),
                )
            ),
            "legacy_stock": (
                mixed.inputs.legacy_stock[0].model_copy(
                    update={
                        "tenant_id": scope.tenant_id,
                        "branch_id": scope.branch_id,
                    }
                ),
            ),
        }
    )
    lines = (
        original.model_copy(
            update={
                "quantity": Decimal("0.001"),
                "original_retail_unit_price": legacy_price,
                "gross_amount": gross,
                "net_amount": gross,
            }
        ),
        *(
            line.model_copy(update={"position": line.position + 1})
            for line in draft.inputs.saved_quote.lines
        ),
    )
    quote = draft.inputs.saved_quote.model_copy(
        update={
            "lines": lines,
            "total_amount": gross + draft.inputs.saved_quote.total_amount,
        }
    )
    return _replace_quote(
        draft.model_copy(update={"inputs": inputs, "legacy_items": (legacy,)}), quote
    )


@pytest.mark.parametrize(
    "sale_format,units,kinds",
    [
        ("pack", 30, ["pack_take"]),
        ("blister", 10, ["pack_open", "blister_take"]),
        ("unit", 1, ["pack_open", "unit_take"]),
    ],
)
def test_each_physical_format_keeps_whole_mixed_quote_and_physical_only_movements(
    sale_format: SaleFormatV2,
    units: int,
    kinds: list[str],
) -> None:
    draft = mixed_draft(((sale_format, 1, Decimal("0.00")),))
    before = draft.model_dump(mode="json")
    plan = build_edge_mixed_cash_plan(draft)
    assert plan.quote == draft.inputs.saved_quote
    assert set(plan.model_dump()) == {"schema_version", "quote", "stock_request_hash", "movements"}
    assert [line.inventory_kind for line in plan.quote.lines] == ["legacy_pack", "physical_unit"]
    assert plan.quote.lines[0].quantity == Decimal("0.001")
    assert [movement.kind for movement in plan.movements] == kinds
    assert sum(movement.units_taken for movement in plan.movements) == units
    assert {movement.line_id for movement in plan.movements} == {draft.unit_items[0].id}
    assert {movement.lot_id for movement in plan.movements} == {draft.inputs.physical_stock[0].id}
    assert draft.model_dump(mode="json") == before
    raw = plan.model_dump(mode="json")
    assert raw["quote"]["lines"][0]["quantity"] == "0.001"
    assert all(key not in json.dumps(raw) for key in ("legacy_allocations", "sealed_start", "cost"))
    assert NativeMixedCashPlanV1.model_validate_json(json.dumps(raw)) == plan
    with pytest.raises(ValidationError):
        NativePhysicalCashPlanV1.model_validate_json(json.dumps(raw))


def test_shared_pack_alias_is_reused_without_legacy_movement_or_second_opening() -> None:
    draft = mixed_draft(
        (
            ("unit", 2, Decimal("0.00")),
            ("pack", 1, Decimal("0.00")),
            ("blister", 1, Decimal("0.00")),
        )
    )
    plan = build_edge_mixed_cash_plan(draft)
    assert [movement.kind for movement in plan.movements] == [
        "pack_take",
        "pack_open",
        "blister_take",
        "unit_take",
    ]
    pack_id = plan.movements[1].pack_id
    assert pack_id is not None and pack_id.version == 5
    assert {movement.pack_id for movement in plan.movements[1:]} == {pack_id}
    assert [movement.entry_order for movement in plan.movements] == [0, 1, 2, 3]
    assert sum(movement.units_taken for movement in plan.movements) == 42
    assert draft.legacy_items[0].id not in {movement.line_id for movement in plan.movements}


def test_existing_fragment_keeps_actual_uuid_and_consumes_fragment_first() -> None:
    draft = mixed_draft()
    lot = draft.inputs.physical_stock[0]
    opened = OpenedPackV2(
        id=uuid4(),
        opened_at=lot.created_at,
        intact_blisters=(2, 3),
        opened_blisters=(OpenedBlisterV2(number=1, remaining_units=4),),
    )
    inputs = draft.inputs.model_copy(
        update={"physical_stock": (lot.model_copy(update={"opened_packs": (opened,)}),)}
    )
    plan = build_edge_mixed_cash_plan(draft.model_copy(update={"inputs": inputs}))
    assert len(plan.movements) == 1
    movement = plan.movements[0]
    assert movement.pack_id == opened.id and movement.kind == "unit_take"
    assert movement.closed_delta == 0 and movement.units_taken == 2
    assert movement.before_state is not None and movement.after_state is not None
    assert movement.before_state.opened_blisters[0].remaining_units == 4
    assert movement.after_state.opened_blisters[0].remaining_units == 2
    assert movement.after_state.intact_blisters == (2, 3)


@pytest.mark.parametrize("price,gross", [("25.00", "0.02"), ("15.00", "0.02"), ("5.00", "0.00")])
def test_legacy_fraction_uses_existing_half_even_facts_under_other_ambient_rounding(
    price: str,
    gross: str,
) -> None:
    draft = mixed_draft(legacy_price=Decimal(price))
    with localcontext() as context:
        context.rounding = ROUND_UP
        plan = build_edge_mixed_cash_plan(draft)
    assert plan.quote.lines[0].quantity == Decimal("0.001")
    assert plan.quote.lines[0].gross_amount == Decimal(gross)


@pytest.mark.parametrize("missing", ["legacy", "physical", "mixed_kind"])
def test_builder_requires_current_mixed_kind_and_both_current_row_families(missing: str) -> None:
    draft = mixed_draft()
    if missing == "legacy":
        draft = draft.model_copy(update={"legacy_items": ()})
    elif missing == "physical":
        draft = draft.model_copy(update={"unit_items": ()})
    else:
        draft = physical_draft((("unit", 2, Decimal("0.00")),))
    with pytest.raises(BusinessRuleError, match="both inventory"):
        build_edge_mixed_cash_plan(draft)


def test_mixed_wire_cannot_accept_a_physical_only_quote() -> None:
    draft = physical_draft((("unit", 2, Decimal("0.00")),))
    plan = physical_module.build_edge_physical_cash_plan(draft)
    with pytest.raises(ValidationError, match="both legacy and physical"):
        NativeMixedCashPlanV1.model_validate(plan.model_dump(mode="python"))


@pytest.mark.parametrize("change", ["quantity", "legacy_total", "physical_quantity"])
def test_builder_rechecks_current_rows_instead_of_trusting_saved_or_constructed_models(
    change: str,
) -> None:
    draft = mixed_draft()
    if change == "physical_quantity":
        draft = draft.model_copy(
            update={"unit_items": (draft.unit_items[0].model_copy(update={"quantity": 3}),)}
        )
    else:
        row = draft.legacy_items[0].model_copy(
            update={
                "quantity" if change == "quantity" else "total_price": Decimal(
                    "0.002" if change == "quantity" else "0.01"
                ),
            }
        )
        draft = draft.model_copy(update={"legacy_items": (row,)})
    with pytest.raises((BusinessRuleError, ConflictError)):
        build_edge_mixed_cash_plan(draft)


@pytest.mark.parametrize("change", ["earlier", "blocked", "shortage", "expired"])
def test_legacy_fefo_and_eligibility_are_rechecked_before_building_oracle(change: str) -> None:
    draft = mixed_draft()
    lot = draft.inputs.legacy_stock[0]
    if change == "earlier":
        batches = (
            lot,
            lot.model_copy(
                update={"id": uuid4(), "expires_at": lot.expires_at - timedelta(days=1)}
            ),
        )
    else:
        updates: dict[str, object] = {
            "blocked": {"is_blocked": True},
            "shortage": {"qty_remaining": Decimal("0.000")},
            "expired": {"expires_at": draft.inputs.stock_date - timedelta(days=1)},
        }[change]
        batches = (lot.model_copy(update=updates),)
    inputs = draft.inputs.model_copy(
        update={
            "legacy_stock": tuple(sorted(batches, key=lambda row: str(row.id))),
        }
    )
    with pytest.raises(BusinessRuleError):
        build_edge_mixed_cash_plan(draft.model_copy(update={"inputs": inputs}))


def changed_discount(draft: EdgeRetailCashDraftV1, *, legacy: bool) -> EdgeRetailCashDraftV1:
    position = 0 if legacy else 1
    lines = list(draft.inputs.saved_quote.lines)
    line = lines[position]
    lines[position] = line.model_copy(
        update={"discount_amount": Decimal("0.01"), "net_amount": line.net_amount - Decimal("0.01")}
    )
    quote = draft.inputs.saved_quote.model_copy(
        update={
            "lines": tuple(lines),
            "total_amount": sum((row.net_amount for row in lines), Decimal("0.00")),
        }
    )
    if legacy:
        draft = draft.model_copy(
            update={
                "legacy_items": (
                    draft.legacy_items[0].model_copy(
                        update={
                            "discount_amount": Decimal("0.01"),
                            "total_price": lines[position].net_amount,
                        }
                    ),
                )
            }
        )
    else:
        draft = draft.model_copy(
            update={
                "unit_items": (
                    draft.unit_items[0].model_copy(
                        update={
                            "discount_amount": Decimal("0.01"),
                        }
                    ),
                )
            }
        )
    return _replace_quote(draft, quote)


def test_stock_hash_uses_physical_subset_and_its_discounts_only() -> None:
    draft = mixed_draft()
    before = build_edge_mixed_cash_plan(draft)
    legacy = build_edge_mixed_cash_plan(changed_discount(draft, legacy=True))
    physical = build_edge_mixed_cash_plan(changed_discount(draft, legacy=False))
    assert legacy.quote != before.quote and legacy.stock_request_hash == before.stock_request_hash
    assert physical.stock_request_hash != before.stock_request_hash


@pytest.mark.parametrize("extra", ["legacy_allocations", "sealed_count", "cost_amount"])
def test_server_owned_allocations_and_costs_are_forbidden_on_wire(extra: str) -> None:
    raw = build_edge_mixed_cash_plan(mixed_draft()).model_dump(mode="json")
    raw[extra] = []
    with pytest.raises(ValidationError):
        NativeMixedCashPlanV1.model_validate_json(json.dumps(raw))


def test_constructed_nested_models_do_not_bypass_integer_or_legacy_precision_validation() -> None:
    draft = mixed_draft()
    forged_unit = draft.unit_items[0].model_copy(update={"quantity": True})
    with pytest.raises(ValidationError):
        build_edge_mixed_cash_plan(draft.model_copy(update={"unit_items": (forged_unit,)}))
    forged_legacy = draft.legacy_items[0].model_copy(update={"quantity": Decimal("0.0001")})
    with pytest.raises(ValidationError):
        build_edge_mixed_cash_plan(draft.model_copy(update={"legacy_items": (forged_legacy,)}))
    plan = build_edge_mixed_cash_plan(draft)
    forged_move = plan.movements[0].model_copy(update={"units_taken": True})
    with pytest.raises(ValidationError):
        NativeMixedCashPlanV1.model_validate(plan.model_copy(update={"movements": (forged_move,)}))


def test_wire_physical_movement_cannot_be_rebound_to_a_legacy_line() -> None:
    draft = mixed_draft()
    plan = build_edge_mixed_cash_plan(draft)
    movement = plan.movements[-1].model_copy(update={"line_id": draft.legacy_items[0].id})
    with pytest.raises(ValidationError, match="every physical position"):
        NativeMixedCashPlanV1.model_validate(
            plan.model_copy(
                update={
                    "movements": (*plan.movements[:-1], movement),
                }
            )
        )


def test_wire_rejects_more_than_8192_movements() -> None:
    raw = build_edge_mixed_cash_plan(mixed_draft()).model_dump(mode="json")
    raw["movements"] = raw["movements"] * 4097
    with pytest.raises(ValidationError) as rejected:
        NativeMixedCashPlanV1.model_validate_json(json.dumps(raw))
    assert any(error["type"] == "too_long" for error in rejected.value.errors())


def test_encoded_whole_quote_and_movement_bounds_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = mixed_draft()
    plan = build_edge_mixed_cash_plan(draft)
    encoded = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
    monkeypatch.setattr(physical_module, "MAX_INPUT_BYTES", len(encoded.encode("utf-8")))
    build_edge_mixed_cash_plan(draft)
    monkeypatch.setattr(physical_module, "MAX_INPUT_BYTES", len(encoded.encode("utf-8")) - 1)
    with pytest.raises(BusinessRuleError, match="wire bound"):
        build_edge_mixed_cash_plan(draft)
    monkeypatch.undo()
    monkeypatch.setattr(physical_module, "MAX_NATIVE_MOVEMENTS", 1)
    with pytest.raises(BusinessRuleError, match="limit"):
        build_edge_mixed_cash_plan(draft)
