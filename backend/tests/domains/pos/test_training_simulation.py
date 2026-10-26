"""Pure POS training simulation: no DB fixtures, persisted effects or payment calls."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, Inexact, Rounded, localcontext
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.inventory.unit_stock import UnitPackaging
from app.domains.inventory.unit_stock_v2 import (
    OpenedPackV2,
    SaleFormatV2,
    UnitPackagingV2,
    UnitStockBatchV2,
    stock_availability_v2,
)
from app.domains.pos.training_simulation import (
    MAX_SNAPSHOT_BATCHES,
    TrainingLegacyBatchSnapshot,
    simulate_training_sale,
)
from app.domains.pos.unit_cart_quote import (
    DraftQuoteLineV2,
    MixedCartQuoteV2,
    PhysicalUnitDraftQuoteLineV2,
    UnitDraftQuoteLineV2,
)
from app.domains.pos.unit_contracts import EXACT_CONTEXT, LegacyResultLineV2

TODAY = date(2026, 10, 26)
CREATED = datetime(2026, 1, 1, tzinfo=UTC)
TENANT, BRANCH, PHYSICAL, LEGACY = (UUID(int=i) for i in range(1, 5))
PACKAGING = UnitPackagingV2(unit="tablet", units_per_pack=30, units_per_blister=10, dispense_step=1)


def physical_line(
    sale_format: SaleFormatV2 = "unit", *, quantity: int = 1, position: int = 1
) -> PhysicalUnitDraftQuoteLineV2:
    size = {"pack": 30, "blister": 10, "unit": 1}[sale_format]
    price = {"pack": Decimal("15"), "blister": Decimal("5"), "unit": Decimal("0.50")}[sale_format]
    return PhysicalUnitDraftQuoteLineV2(
        id=UUID(int=100 + position),
        position=position,
        catalog_id=PHYSICAL,
        packaging=PACKAGING,
        sale_format=sale_format,
        quantity=quantity,
        base_units=quantity * size,
        original_retail_unit_price=price,
        gross_amount=price * quantity,
        discount_amount=Decimal("0.00"),
        net_amount=price * quantity,
    )


def legacy_line(
    *, quantity: str = "0.125", position: int = 2, batch: int = 201
) -> LegacyResultLineV2:
    with localcontext(EXACT_CONTEXT):
        gross = (Decimal("10.00") * Decimal(quantity)).quantize(Decimal("0.01"))
    return LegacyResultLineV2(
        inventory_kind="legacy_pack",
        id=UUID(int=100 + position),
        position=position,
        catalog_id=LEGACY,
        batch_id=UUID(int=batch),
        quantity=Decimal(quantity),
        original_retail_unit_price=Decimal("10.00"),
        gross_amount=gross,
        discount_amount=Decimal("0.00"),
        net_amount=gross,
    )


def quote_for(*lines: DraftQuoteLineV2) -> MixedCartQuoteV2:
    with localcontext(EXACT_CONTEXT):
        total = sum((line.net_amount for line in lines), Decimal("0.00"))
    return MixedCartQuoteV2(
        tenant_id=TENANT,
        branch_id=BRANCH,
        sale_id=UUID(int=5),
        register_id=UUID(int=6),
        shift_id=UUID(int=7),
        cashier_id=UUID(int=8),
        lines=lines,
        total_amount=total,
    )


def physical_stock(*, batch: int = 200, closed_packs: int = 2) -> UnitStockBatchV2:
    return UnitStockBatchV2(
        id=UUID(int=batch),
        tenant_id=TENANT,
        branch_id=BRANCH,
        catalog_id=PHYSICAL,
        expires_at=TODAY + timedelta(days=90),
        created_at=CREATED,
        packaging=PACKAGING,
        closed_packs=closed_packs,
        opened_packs=(),
    )


def legacy_stock(*, batch: int = 201, quantity: str = "1.000") -> TrainingLegacyBatchSnapshot:
    return TrainingLegacyBatchSnapshot(
        id=UUID(int=batch),
        tenant_id=TENANT,
        branch_id=BRANCH,
        catalog_id=LEGACY,
        expires_at=TODAY + timedelta(days=90),
        created_at=CREATED,
        quantity_remaining=Decimal(quantity),
    )


@pytest.mark.parametrize("sale_format,remaining", [("pack", 30), ("blister", 50), ("unit", 59)])
def test_physical_formats_use_existing_planner_without_mutating_inputs(
    sale_format: SaleFormatV2, remaining: int
) -> None:
    quote, stock = quote_for(physical_line(sale_format)), physical_stock()
    before = quote.model_dump_json(), stock.model_dump_json()
    result = simulate_training_sale(quote=quote, physical_batches=(stock,), today=TODAY)
    available = stock_availability_v2(
        batches=result.physical_after,
        tenant_id=TENANT,
        branch_id=BRANCH,
        catalog_id=PHYSICAL,
        packaging=PACKAGING,
        today=TODAY,
    )
    assert available.base_units == remaining
    assert result.total_amount == quote.total_amount
    assert result.physical_before == (stock,)
    assert (quote.model_dump_json(), stock.model_dump_json()) == before
    assert result == simulate_training_sale(quote=quote, physical_batches=(stock,), today=TODAY)


def test_mixed_repeated_rows_share_legacy_stock_and_serialize_exact_retail() -> None:
    quote = quote_for(physical_line(), legacy_line(), legacy_line(position=3))
    physical, legacy = physical_stock(), legacy_stock()
    before = physical.model_dump_json(), legacy.model_dump_json()
    result = simulate_training_sale(
        quote=quote, physical_batches=(physical,), legacy_batches=(legacy,), today=TODAY
    )
    assert result.legacy_after[0].quantity_remaining == Decimal("0.750")
    assert result.total_amount == Decimal("3.00")
    payload = result.model_dump(mode="json")
    assert payload["is_test"] is True
    assert payload["state"] == "simulated_training_sale"
    assert payload["total_amount"] == "3.00"
    assert payload["legacy_after"][0]["quantity_remaining"] == "0.750"
    assert not {"stock_command_id", "payment_id", "receipt_number", "operation_id"} & payload.keys()
    assert (physical.model_dump_json(), legacy.model_dump_json()) == before


def test_mixed_fefo_spans_batches_and_preserves_quoted_discounts() -> None:
    physical = physical_line().model_copy(
        update={"discount_amount": Decimal("0.10"), "net_amount": Decimal("0.40")}
    )
    first = legacy_line().model_copy(
        update={"discount_amount": Decimal("0.25"), "net_amount": Decimal("1.00")}
    )
    second = legacy_line(quantity="0.750", position=3, batch=202).model_copy(
        update={"discount_amount": Decimal("0.50"), "net_amount": Decimal("7.00")}
    )
    quote = quote_for(physical, first, second)
    batches = legacy_stock(quantity="0.125"), legacy_stock(batch=202)
    before = tuple(batch.model_dump_json() for batch in batches)
    result = simulate_training_sale(
        quote=quote,
        physical_batches=(physical_stock(),),
        legacy_batches=batches,
        today=TODAY,
    )
    assert result.total_amount == Decimal("8.40")
    assert result.quote == quote
    assert tuple(batch.quantity_remaining for batch in result.legacy_after) == (
        Decimal("0.000"),
        Decimal("0.250"),
    )
    assert tuple(batch.model_dump_json() for batch in batches) == before


@pytest.mark.parametrize("inventory", ["legacy", "physical"])
def test_shortage_returns_no_partial_result_or_modified_snapshot(inventory: str) -> None:
    quote = quote_for(
        physical_line("pack"),
        legacy_line(quantity="0.750"),
        legacy_line(quantity="0.750", position=3),
    )
    physical = physical_stock(closed_packs=0 if inventory == "physical" else 2)
    legacy = legacy_stock(quantity="1.000" if inventory == "legacy" else "2.000")
    before = physical.model_dump_json(), legacy.model_dump_json()
    with pytest.raises(ValueError, match="stock|available|allocate"):
        simulate_training_sale(
            quote=quote, physical_batches=(physical,), legacy_batches=(legacy,), today=TODAY
        )
    assert (physical.model_dump_json(), legacy.model_dump_json()) == before


def test_repeated_physical_positions_share_stock_instead_of_reusing_one_pack() -> None:
    quote = quote_for(physical_line("pack"), physical_line("pack", position=2))
    with pytest.raises(ValueError):
        simulate_training_sale(
            quote=quote, physical_batches=(physical_stock(closed_packs=1),), today=TODAY
        )


def test_stock_input_order_does_not_change_virtual_result() -> None:
    quote = quote_for(physical_line(), legacy_line())
    physical = physical_stock(batch=202), physical_stock()
    legacy = legacy_stock(batch=203), legacy_stock()
    first = simulate_training_sale(
        quote=quote, physical_batches=physical, legacy_batches=legacy, today=TODAY
    )
    reverse = simulate_training_sale(
        quote=quote, physical_batches=physical[::-1], legacy_batches=legacy[::-1], today=TODAY
    )
    assert first.model_dump_json() == reverse.model_dump_json()


@pytest.mark.parametrize("inventory", ["legacy", "physical"])
@pytest.mark.parametrize("change", ["blocked", "expired", "scope", "catalog", "duplicate"])
def test_snapshot_eligibility_and_identity_are_checked_before_filtering(
    inventory: str, change: str
) -> None:
    quote = quote_for(physical_line(), legacy_line())
    physical, legacy = physical_stock(), legacy_stock()
    changes: dict[str, object] = {
        "blocked": {"is_blocked": True},
        "expired": {"expires_at": TODAY},
        "scope": {"tenant_id": UUID(int=999), "is_blocked": True},
        "catalog": {"catalog_id": UUID(int=999), "is_blocked": True},
        "duplicate": {},
    }
    update = cast(dict[str, object], changes[change])
    if inventory == "physical":
        physical = UnitStockBatchV2.model_validate({**physical.model_dump(), **update})
    else:
        legacy = TrainingLegacyBatchSnapshot.model_validate({**legacy.model_dump(), **update})
    with pytest.raises(ValueError):
        simulate_training_sale(
            quote=quote,
            physical_batches=(
                (physical, physical)
                if inventory == "physical" and change == "duplicate"
                else (physical,)
            ),
            legacy_batches=(
                (legacy, legacy) if inventory == "legacy" and change == "duplicate" else (legacy,)
            ),
            today=TODAY,
        )


def test_legacy_selected_batch_must_match_aggregate_fefo() -> None:
    earlier = legacy_stock(batch=202).model_copy(update={"expires_at": TODAY + timedelta(days=1)})
    with pytest.raises(ValueError, match="stale"):
        simulate_training_sale(
            quote=quote_for(physical_line(), legacy_line()),
            physical_batches=(physical_stock(),),
            legacy_batches=(legacy_stock(), earlier),
            today=TODAY,
        )


def test_incompatible_physical_packaging_cannot_supply_the_cart() -> None:
    stock = physical_stock().model_copy(
        update={"packaging": PACKAGING.model_copy(update={"units_per_pack": 60})}
    )
    with pytest.raises(ValueError):
        simulate_training_sale(
            quote=quote_for(physical_line()), physical_batches=(stock,), today=TODAY
        )


def test_duplicate_opened_identity_is_rejected_even_in_blocked_batch() -> None:
    opened = OpenedPackV2(id=UUID(int=300), opened_at=CREATED, intact_blisters=(1, 2, 3))
    first = physical_stock().model_copy(update={"opened_packs": (opened,)})
    second = physical_stock(batch=202).model_copy(
        update={"opened_packs": (opened,), "is_blocked": True}
    )
    with pytest.raises(ValueError, match="globally unique"):
        simulate_training_sale(
            quote=quote_for(physical_line()), physical_batches=(first, second), today=TODAY
        )


@pytest.mark.parametrize(
    "quantity", [0.1, True, "0.0001", "NaN", "Infinity", "-1", "100000000000.000"]
)
def test_legacy_stock_decimal_is_strict_and_bounded(quantity: object) -> None:
    with pytest.raises(ValidationError):
        TrainingLegacyBatchSnapshot.model_validate(
            {**legacy_stock().model_dump(), "quantity_remaining": quantity}
        )


def test_zero_legacy_stock_is_valid_but_cannot_supply_demand() -> None:
    stock = legacy_stock(quantity="0.000")
    with pytest.raises(ValueError, match="insufficient legacy"):
        simulate_training_sale(
            quote=quote_for(physical_line(), legacy_line()),
            physical_batches=(physical_stock(),),
            legacy_batches=(stock,),
            today=TODAY,
        )


def test_revalidates_model_construct_and_copy_inputs() -> None:
    quote = quote_for(physical_line())
    with pytest.raises(ValidationError):
        simulate_training_sale(
            quote=quote.model_copy(update={"total_amount": Decimal("999")}),
            physical_batches=(physical_stock(),),
            today=TODAY,
        )
    invalid = physical_stock().model_copy(update={"closed_packs": True})
    with pytest.raises(ValidationError):
        simulate_training_sale(quote=quote, physical_batches=(invalid,), today=TODAY)
    legacy = legacy_stock().model_copy(update={"quantity_remaining": 0.5})
    with (
        pytest.warns(UserWarning, match="Pydantic serializer warnings"),
        pytest.raises(ValidationError),
    ):
        simulate_training_sale(
            quote=quote_for(physical_line(), legacy_line()),
            physical_batches=(physical_stock(),),
            legacy_batches=(legacy,),
            today=TODAY,
        )


def test_prepared_and_legacy_only_carts_are_not_training_physical_simulation() -> None:
    prepared = UnitDraftQuoteLineV2(
        id=UUID(int=102),
        position=2,
        catalog_id=LEGACY,
        packaging=UnitPackaging(unit="tablet", units_per_pack=30, dispense_step=1),
        sale_format="unit",
        quantity=1,
        base_units=1,
        original_retail_unit_price=Decimal("0.50"),
        gross_amount=Decimal("0.50"),
        discount_amount=Decimal("0.00"),
        net_amount=Decimal("0.50"),
    )
    for quote in (quote_for(legacy_line(position=1)), quote_for(physical_line(), prepared)):
        with pytest.raises(ValueError, match="requires physical"):
            simulate_training_sale(quote=quote, physical_batches=(physical_stock(),), today=TODAY)


def test_argument_types_and_snapshot_tuple_bounds() -> None:
    quote = quote_for(physical_line())
    for invalid in ([], (object(),), (physical_stock(),) * (MAX_SNAPSHOT_BATCHES + 1)):
        with pytest.raises(ValueError):
            simulate_training_sale(
                quote=quote,
                physical_batches=cast(tuple[UnitStockBatchV2, ...], invalid),
                today=TODAY,
            )
    with pytest.raises(ValueError, match="calendar date"):
        simulate_training_sale(
            quote=quote, physical_batches=(physical_stock(),), today=cast(date, CREATED)
        )


def test_decimal_ambient_precision_and_traps_do_not_change_simulation() -> None:
    quote = quote_for(physical_line(), legacy_line())
    physical, legacy = (physical_stock(),), (legacy_stock(),)
    expected = simulate_training_sale(
        quote=quote, physical_batches=physical, legacy_batches=legacy, today=TODAY
    )
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        result = simulate_training_sale(
            quote=quote, physical_batches=physical, legacy_batches=legacy, today=TODAY
        )
    assert result == expected
