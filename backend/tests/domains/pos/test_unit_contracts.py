"""Pure versioned checkout boundaries and original-snapshot document arithmetic."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, Inexact, localcontext
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.inventory.unit_stock import UnitPackaging
from app.domains.pos.schemas import ReceiptData, SaleCheckoutRequest, SaleCheckoutResult
from app.domains.pos.service import POSService
from app.domains.pos.unit_contracts import (
    CheckoutPaymentV2,
    CheckoutRequestV2,
    CheckoutResultV2,
    CheckoutScopeV2,
    LegacyCheckoutLineV2,
    LegacyResultLineV2,
    PreparedCheckoutLineV2,
    PreparedResultLineV2,
    build_checkout_result,
    prepared_result_line,
)
from app.domains.pos.unit_documents import (
    ReceiptDocumentV2,
    RefundQuoteV2,
    build_receipt,
    build_refund_quote,
    build_refund_receipt_line,
    canonical_hash_v2,
    canonical_json_v2,
)
from app.domains.pos.unit_line_service import UnitSaleLineSnapshot


def uid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012d}")


def snapshot(*, sale_format: str = "unit") -> UnitSaleLineSnapshot:
    return UnitSaleLineSnapshot(
        id=uid(10),
        command_id=uid(2),
        tenant_id=uid(1),
        branch_id=uid(3),
        catalog_id=uid(4),
        stock_operation_id=uid(11),
        version=1,
        unit="tablet",
        units_per_pack=30,
        dispense_step=1,
        sale_format="pack" if sale_format == "pack" else "unit",
        quantity=3,
        base_units=90 if sale_format == "pack" else 3,
        original_retail_unit_price=Decimal("10.00") if sale_format == "pack" else Decimal("1.00"),
        gross_amount=Decimal("30.00") if sale_format == "pack" else Decimal("3.00"),
        discount_amount=Decimal("0.01"),
        net_amount=Decimal("29.99") if sale_format == "pack" else Decimal("2.99"),
        original_cost_amount=Decimal("1.00"),
        money_version=1,
        currency="TJS",
    )


def scope() -> CheckoutScopeV2:
    return CheckoutScopeV2(
        tenant_id=uid(1),
        branch_id=uid(3),
        register_id=uid(5),
        shift_id=uid(6),
        cashier_id=uid(7),
        event_id=uid(8),
        sale_id=uid(9),
        operation_id=uid(2),
        created_at=datetime(2026, 10, 4, 10, 0, tzinfo=UTC),
        completed_at=datetime(2026, 10, 4, 10, 1, tzinfo=UTC),
        receipt_number="000001",
        receipt_sequence=1,
        is_test=True,
    )


def request_payload() -> dict[str, object]:
    return {
        "schema_version": 2,
        "operation_id": str(uid(2)),
        "register_id": str(uid(5)),
        "lines": [
            {"inventory_kind": "legacy_pack", "catalog_id": str(uid(12)), "quantity": "1.250"},
            {
                "inventory_kind": "prepared_unit",
                "catalog_id": str(uid(4)),
                "sale_format": "unit",
                "quantity": 3,
            },
        ],
        "payments": [{"id": str(uid(13)), "method": "cash", "amount": "10.00"}],
    }


def result() -> CheckoutResultV2:
    legacy = LegacyResultLineV2(
        inventory_kind="legacy_pack",
        id=uid(15),
        position=1,
        catalog_id=uid(12),
        batch_id=uid(16),
        quantity=Decimal("1.250"),
        original_retail_unit_price=Decimal("2.00"),
        gross_amount=Decimal("2.50"),
        discount_amount=Decimal("0.00"),
        net_amount=Decimal("2.50"),
    )
    return build_checkout_result(
        scope=scope(),
        legacy_lines=(legacy,),
        prepared_lines=((uid(14), 2, snapshot()),),
        payments=(CheckoutPaymentV2(id=uid(13), method="cash", amount=Decimal("5.49")),),
        total_amount=Decimal("5.49"),
    )


def test_mixed_request_retains_explicit_formats_and_legacy_fractional_quantity() -> None:
    request = CheckoutRequestV2.model_validate(request_payload())
    legacy, prepared = request.lines
    assert isinstance(legacy, LegacyCheckoutLineV2)
    assert isinstance(prepared, PreparedCheckoutLineV2)
    assert legacy.quantity == Decimal("1.250")
    assert prepared.quantity == 3 and prepared.sale_format == "unit"
    assert isinstance(request.lines, tuple) and isinstance(request.payments, tuple)
    assert canonical_hash_v2(request) == canonical_hash_v2(
        CheckoutRequestV2.model_validate_json(canonical_json_v2(request))
    )


@pytest.mark.parametrize("version", [1, 3, True, "2", 2.0])
def test_unknown_or_coerced_version_is_rejected(version: object) -> None:
    payload = request_payload()
    payload["schema_version"] = version
    with pytest.raises(ValidationError):
        CheckoutRequestV2.model_validate(payload)


@pytest.mark.parametrize("quantity", [True, "3", 3.0, Decimal("3"), 1.5, 0])
def test_prepared_quantity_is_a_strict_positive_integer(quantity: object) -> None:
    with pytest.raises(ValidationError):
        PreparedCheckoutLineV2.model_validate(
            {
                "inventory_kind": "prepared_unit",
                "catalog_id": str(uid(4)),
                "sale_format": "unit",
                "quantity": quantity,
            }
        )


@pytest.mark.parametrize(
    "field,value", [("qty", "1"), ("price", "1.00"), ("purchase_cost", "0.00")]
)
def test_client_cannot_supply_legacy_qty_or_server_money_fields(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        PreparedCheckoutLineV2.model_validate(
            {
                "inventory_kind": "prepared_unit",
                "catalog_id": str(uid(4)),
                "sale_format": "unit",
                "quantity": 1,
                field: value,
            }
        )


@pytest.mark.parametrize("format_payload", [{}, {"sale_format": "tablet"}])
def test_prepared_format_is_required_and_explicit(format_payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PreparedCheckoutLineV2.model_validate(
            {
                "inventory_kind": "prepared_unit",
                "catalog_id": str(uid(4)),
                "quantity": 1,
                **format_payload,
            }
        )


@pytest.mark.parametrize("kind", ["prepared", None])
def test_unknown_inventory_discriminator_never_falls_back_to_legacy(kind: object) -> None:
    payload = request_payload()
    payload["lines"] = [{"inventory_kind": kind, "catalog_id": str(uid(4)), "quantity": 1}]
    with pytest.raises(ValidationError):
        CheckoutRequestV2.model_validate(payload)


@pytest.mark.parametrize(
    "amount", ["NaN", "Infinity", "-0.01", "1000000000000.00", "1.001", True, 0.1]
)
def test_money_rejects_noncanonical_or_unsafe_values(amount: object) -> None:
    with pytest.raises(ValidationError):
        CheckoutPaymentV2.model_validate({"id": str(uid(13)), "method": "cash", "amount": amount})


def test_nested_contracts_are_immutable() -> None:
    request = CheckoutRequestV2.model_validate(request_payload())
    with pytest.raises(ValidationError):
        request.schema_version = 3
    with pytest.raises(ValidationError):
        request.lines[1].discount_amount = Decimal("9.00")
    with pytest.raises(ValidationError):
        request.payments[0].amount = Decimal("99.00")


def test_request_hash_is_canonical_but_distinguishes_explicit_formats() -> None:
    first = CheckoutRequestV2.model_validate(request_payload())
    reordered = dict(reversed(list(request_payload().items())))
    reordered["payments"] = [{"amount": "10.0", "method": "cash", "id": str(uid(13))}]
    assert canonical_hash_v2(first) == canonical_hash_v2(
        CheckoutRequestV2.model_validate(reordered)
    )
    changed = request_payload()
    changed["lines"] = [
        {"inventory_kind": "legacy_pack", "catalog_id": str(uid(12)), "quantity": "1.250"},
        {
            "inventory_kind": "prepared_unit",
            "catalog_id": str(uid(4)),
            "sale_format": "pack",
            "quantity": 3,
        },
    ]
    assert canonical_hash_v2(first) != canonical_hash_v2(CheckoutRequestV2.model_validate(changed))


def test_result_and_payment_sums_are_exact_in_mixed_document() -> None:
    document = result()
    assert document.total_amount == Decimal("5.49")
    assert sum(line.net_amount for line in document.lines) == document.total_amount
    assert sum(payment.amount for payment in document.payments) == document.total_amount
    assert isinstance(document.lines, tuple) and isinstance(document.payments, tuple)
    assert document.lines[0].quantity == Decimal("1.250")
    assert isinstance(document.lines[1], PreparedResultLineV2)
    assert document.lines[1].base_units == 3
    with pytest.raises(ValidationError):
        document.lines[1].packaging.units_per_pack = 60


@pytest.mark.parametrize("quantity,gross", [("1.005", "1.00"), ("1.015", "1.02")])
def test_legacy_fractional_quantity_keeps_half_even_retail_rounding(
    quantity: str, gross: str
) -> None:
    line = LegacyResultLineV2(
        inventory_kind="legacy_pack",
        id=uid(15),
        position=1,
        catalog_id=uid(12),
        batch_id=uid(16),
        quantity=Decimal(quantity),
        original_retail_unit_price=Decimal("1.00"),
        gross_amount=Decimal(gross),
        discount_amount=Decimal("0.00"),
        net_amount=Decimal(gross),
    )
    assert line.quantity == Decimal(quantity)
    assert line.gross_amount == Decimal(gross)


@pytest.mark.parametrize("scope_field", ["tenant_id", "branch_id"])
def test_foreign_snapshot_scope_is_rejected(scope_field: str) -> None:
    foreign = replace(snapshot(), **{scope_field: uid(99)})
    with pytest.raises(ValueError):
        build_checkout_result(
            scope=scope(),
            prepared_lines=((uid(14), 1, foreign),),
            payments=(CheckoutPaymentV2(id=uid(13), method="cash", amount=Decimal("2.99")),),
            total_amount=Decimal("2.99"),
        )


@pytest.mark.parametrize(
    "field,value", [("gross_amount", "3.01"), ("net_amount", "1.00"), ("base_units", 30)]
)
def test_result_line_rejects_inconsistent_snapshot_arithmetic(field: str, value: object) -> None:
    payload = prepared_result_line(snapshot(), id=uid(14), position=1).model_dump(mode="json")
    payload[field] = value
    with pytest.raises(ValidationError):
        PreparedResultLineV2.model_validate(payload)


@pytest.mark.parametrize(
    "corruption",
    ["total", "payment", "line_id", "position", "payment_id", "prepared_ref", "stock_ref"],
)
def test_document_rejects_wrong_sums_duplicate_ids_references_and_positions(
    corruption: str,
) -> None:
    payload = result().model_dump(mode="json")
    lines = payload["lines"]
    payments = payload["payments"]
    assert isinstance(lines, list) and isinstance(payments, list)
    if corruption == "total":
        payload["total_amount"] = "5.50"
    elif corruption == "payment":
        payments[0]["amount"] = "5.50"
    elif corruption == "line_id":
        lines[1]["id"] = lines[0]["id"]
    elif corruption == "position":
        lines[1]["position"] = lines[0]["position"]
    elif corruption == "payment_id":
        payments[0]["amount"] = "2.00"
        payments.append({**payments[0], "amount": "3.49"})
    else:
        second = dict(lines[1])
        second["id"] = str(uid(19))
        second["position"] = 3
        if corruption == "prepared_ref":
            second["stock_operation_id"] = str(uid(20))
        else:
            second["prepared_line_id"] = str(uid(20))
        lines.append(second)
        payload["total_amount"] = "8.48"
        payments[0]["amount"] = "8.48"
    with pytest.raises(ValidationError):
        CheckoutResultV2.model_validate(payload)


def test_money_serialization_hash_and_math_ignore_ambient_decimal_precision() -> None:
    original = result()
    with localcontext() as ambient:
        ambient.prec = 1
        ambient.traps[Inexact] = True
        rebuilt = result()
        encoded = canonical_json_v2(rebuilt)
        digest = canonical_hash_v2(rebuilt)
    assert rebuilt == original
    assert encoded == canonical_json_v2(original)
    assert digest == canonical_hash_v2(original)
    assert '"total_amount":"5.49"' in encoded


def test_legacy_v1_json_and_hash_fixture_are_unchanged() -> None:
    request = SaleCheckoutRequest.model_validate(
        {
            "operation_id": str(uid(2)),
            "register_id": str(uid(3)),
            "items": [{"catalog_id": str(uid(4)), "qty": "1.250"}],
            "payments": [{"payment_method": "cash", "amount": "12.50"}],
        }
    )
    assert request.model_dump_json() == (
        '{"operation_id":"00000000-0000-4000-8000-000000000002",'
        '"register_id":"00000000-0000-4000-8000-000000000003","draft_sale_id":null,'
        '"items":[{"catalog_id":"00000000-0000-4000-8000-000000000004","qty":"1.250"}],'
        '"payments":[{"payment_method":"cash","amount":"12.50","metadata":null,'
        '"payment_attempt_id":null}],"prescription":null,"expired_sale_confirmed":false}'
    )
    assert (
        POSService._checkout_operation_hash(
            register_id=uid(3),
            draft_sale_id=None,
            items=[(uid(4), Decimal("1.250"))],
            payments=[("cash", Decimal("12.50"), None, None)],
            prescription=None,
        )
        == "a584462fe16a6fc00909bb0f54f46528edf2be31915c55d482acdfc6a04b7a8d"
    )
    with pytest.raises(ValidationError):
        SaleCheckoutRequest.model_validate(request_payload())


def test_legacy_v1_event_json_fixture_preserves_old_field_names_and_decimals() -> None:
    fixture = {
        "event_id": "00000000-0000-4000-8000-000000000008",
        "sale_id": "00000000-0000-4000-8000-000000000009",
        "operation_id": "00000000-0000-4000-8000-000000000002",
        "tenant_id": "00000000-0000-4000-8000-000000000001",
        "branch_id": "00000000-0000-4000-8000-000000000003",
        "register_id": "00000000-0000-4000-8000-000000000005",
        "shift_id": "00000000-0000-4000-8000-000000000006",
        "cashier_user_id": "00000000-0000-4000-8000-000000000007",
        "receipt_number": "000001",
        "receipt_seq": 1,
        "created_at": "2026-10-04T10:00:00Z",
        "completed_at": "2026-10-04T10:01:00Z",
        "total_amount": "2.50",
        "currency": "TJS",
        "is_test": True,
        "items": [
            {
                "id": "00000000-0000-4000-8000-000000000015",
                "catalog_id": "00000000-0000-4000-8000-000000000012",
                "batch_id": "00000000-0000-4000-8000-000000000016",
                "qty": "1.250",
                "unit_price": "2.00",
                "total_price": "2.50",
                "currency": "TJS",
                "discount_amount": "0.00",
                "position": 1,
            }
        ],
        "payments": [
            {
                "id": "00000000-0000-4000-8000-000000000013",
                "payment_method": "cash",
                "amount": "2.50",
                "currency": "TJS",
                "payment_attempt_id": None,
                "payment_attempt_status": None,
            }
        ],
    }
    assert SaleCheckoutResult.model_validate(fixture).model_dump(mode="json") == fixture
    with pytest.raises(ValidationError):
        SaleCheckoutResult.model_validate({**fixture, "schema_version": 2})


def test_legacy_v1_receipt_json_fixture_preserves_fractional_pack_display() -> None:
    fixture = {
        "sale_id": "00000000-0000-4000-8000-000000000009",
        "is_refund": False,
        "status": "completed",
        "pharmacy_name": "Аптека",
        "branch_name": "Основной",
        "branch_address": None,
        "branch_license": None,
        "receipt_number": "000001",
        "original_receipt_number": None,
        "datetime": "2026-10-04T10:01:00Z",
        "cashier_name": None,
        "items": [
            {
                "position": 1,
                "name": "Препарат",
                "qty": "1.250",
                "unit_price": "2.00",
                "discount_amount": "0.00",
                "total_price": "2.50",
            }
        ],
        "discount_total": "0.00",
        "total": "2.50",
        "currency": "TJS",
        "payments": [{"method": "cash", "amount": "2.50"}],
        "paid_total": "2.50",
        "change": "0.00",
    }
    assert ReceiptData.model_validate(fixture).model_dump(mode="json") == fixture


def test_receipt_retains_mixed_quantity_semantics_without_procurement_or_internal_ids() -> None:
    receipt = build_receipt(result(), names={uid(12): "Упаковочный препарат", uid(4): "Таблетки"})
    assert receipt.lines[0].quantity == Decimal("1.250")
    assert receipt.lines[0].unit_label == "уп."
    assert receipt.lines[1].quantity == 3
    assert receipt.lines[1].unit_label == "табл."
    assert receipt.lines[1].net_amount == Decimal("2.99")
    encoded = canonical_json_v2(receipt)
    for forbidden in (
        "original_cost_amount",
        "catalog_id",
        "stock_operation_id",
        "prepared_line_id",
        "tenant_id",
        "batch_id",
    ):
        assert forbidden not in encoded
    assert isinstance(receipt.lines, tuple) and isinstance(receipt.payments, tuple)
    payload = receipt.model_dump(mode="json")
    lines = payload["lines"]
    assert isinstance(lines, list)
    lines[1]["quantity"] = 4
    with pytest.raises(ValidationError):
        ReceiptDocumentV2.model_validate(payload)


@pytest.mark.parametrize(
    "unit,label",
    [
        ("tablet", "табл."),
        ("capsule", "капс."),
        ("ampoule", "амп."),
        ("sachet", "саше"),
        ("suppository", "супп."),
    ],
)
def test_original_packaging_selects_russian_receipt_unit_label(unit: str, label: str) -> None:
    original = replace(snapshot(), unit=unit)
    document = build_checkout_result(
        scope=scope(),
        prepared_lines=((uid(14), 1, original),),
        payments=(CheckoutPaymentV2(id=uid(13), method="cash", amount=Decimal("2.99")),),
        total_amount=Decimal("2.99"),
    )
    assert build_receipt(document, names={uid(4): "Препарат"}).lines[0].unit_label == label


def test_refund_quotes_allocate_original_net_discount_and_last_cent_without_mutation() -> None:
    original = prepared_result_line(snapshot(), id=uid(14), position=1)
    before = original.model_dump_json()
    parts = [
        build_refund_quote(original, quantity=1, allocation_offset=offset) for offset in range(3)
    ]
    assert [part.net_amount for part in parts] == [
        Decimal("0.99"),
        Decimal("1.00"),
        Decimal("1.00"),
    ]
    assert [part.discount_amount for part in parts] == [
        Decimal("0.01"),
        Decimal("0.00"),
        Decimal("0.00"),
    ]
    assert all(part.gross_amount == Decimal("1.00") for part in parts)
    assert sum(part.net_amount for part in parts) == original.net_amount
    assert sum(part.discount_amount for part in parts) == original.discount_amount
    assert sum(part.gross_amount for part in parts) == original.gross_amount
    grouped = build_refund_quote(original, quantity=2, allocation_offset=1)
    assert grouped.net_amount == parts[1].net_amount + parts[2].net_amount
    assert grouped.kind == "refund_quote"
    assert grouped.original_line == original
    assert original.model_dump_json() == before
    assert build_refund_quote(original, quantity=2, allocation_offset=1) == grouped
    display = build_refund_receipt_line(parts[-1], name="Таблетки")
    assert display.unit_label == "табл." and display.quantity == 1
    assert display.net_amount == Decimal("1.00")
    assert display.original_quantity == 3 and display.allocation_offset == 2
    assert "original_cost_amount" not in canonical_json_v2(display)


def test_pack_refund_keeps_original_pack_format_instead_of_converting_quantity_to_units() -> None:
    original = prepared_result_line(snapshot(sale_format="pack"), id=uid(14), position=1)
    quote = build_refund_quote(original, quantity=1, allocation_offset=2)
    assert quote.base_units == 30
    assert quote.net_amount == Decimal("10.00")
    display = build_refund_receipt_line(quote, name="Препарат")
    assert display.sale_format == "pack" and display.unit_label == "уп."
    assert display.quantity == 1
    with pytest.raises(ValueError):
        build_refund_quote(original, quantity=30, allocation_offset=0)


@pytest.mark.parametrize("quantity,offset", [(0, 0), (1, -1), (2, 2), (1, True)])
def test_refund_quote_rejects_nonoriginal_or_out_of_range_allocation(
    quantity: int, offset: int
) -> None:
    original = prepared_result_line(snapshot(), id=uid(14), position=1)
    with pytest.raises(ValueError):
        build_refund_quote(original, quantity=quantity, allocation_offset=offset)


@pytest.mark.parametrize("quantity,offset", [(1, 0), (2, 1)])
def test_refund_range_respects_the_original_dispense_step(quantity: int, offset: int) -> None:
    source = replace(
        snapshot(),
        dispense_step=2,
        quantity=6,
        base_units=6,
        gross_amount=Decimal("6.00"),
        net_amount=Decimal("5.99"),
    )
    original = prepared_result_line(source, id=uid(14), position=1)
    with pytest.raises(ValueError):
        build_refund_quote(original, quantity=quantity, allocation_offset=offset)


def test_refund_quote_rejects_balanced_but_forged_money_and_origin_price() -> None:
    original = prepared_result_line(snapshot(), id=uid(14), position=1)
    quote = build_refund_quote(original, quantity=1, allocation_offset=0)
    payload = quote.model_dump(mode="json")
    with pytest.raises(ValidationError):
        RefundQuoteV2.model_validate({**payload, "net_amount": "0.98", "gross_amount": "0.98"})
    with pytest.raises(ValidationError):
        RefundQuoteV2.model_validate({**payload, "original_retail_unit_price": "9.00"})


@pytest.mark.parametrize("timestamp", [1234, True, "1234", "2026-10-04T10:00:00"])
def test_document_times_reject_epoch_values_and_naive_dates(timestamp: object) -> None:
    payload = scope().model_dump()
    with pytest.raises(ValidationError):
        CheckoutScopeV2.model_validate({**payload, "created_at": timestamp})
    receipt = build_receipt(result(), names={uid(4): "Таблетки", uid(12): "Упаковка"})
    with pytest.raises(ValidationError):
        type(receipt).model_validate({**receipt.model_dump(), "completed_at": timestamp})


def test_packaging_version_is_required_and_constructed_instances_are_revalidated() -> None:
    original = prepared_result_line(snapshot(), id=uid(14), position=1)
    payload = original.model_dump()
    packaging = original.packaging.model_dump()
    packaging.pop("version")
    with pytest.raises(ValidationError):
        PreparedResultLineV2.model_validate({**payload, "packaging": packaging})
    forged = UnitPackaging.model_construct(
        version=1, unit="tablet", units_per_pack=30, dispense_step=7
    )
    with pytest.raises(ValidationError):
        PreparedResultLineV2.model_validate({**payload, "packaging": forged})


def test_legacy_receipt_quantity_has_one_canonical_representation() -> None:
    receipt = build_receipt(result(), names={uid(4): "Таблетки", uid(12): "Упаковка"})
    line = receipt.lines[0].model_dump()
    normalized = []
    for quantity in (1, "1", Decimal("1.000")):
        normalized.append(
            type(receipt.lines[0])
            .model_validate(
                {
                    **line,
                    "quantity": quantity,
                    "gross_amount": "2.00",
                    "net_amount": "2.00",
                }
            )
            .model_dump(mode="json")
        )
    assert normalized[0] == normalized[1] == normalized[2]
    assert normalized[0]["quantity"] == "1.000"


@pytest.mark.parametrize("count", [2, 3, 7])
@pytest.mark.parametrize("price", [Decimal("0.01"), Decimal("1.11")])
def test_refund_partition_preserves_displayed_price_and_all_original_totals(
    count: int, price: Decimal
) -> None:
    gross = price * count
    for discount in (Decimal("0.00"), Decimal("0.01"), gross - Decimal("0.01")):
        source = replace(
            snapshot(),
            quantity=count,
            base_units=count,
            original_retail_unit_price=price,
            gross_amount=gross,
            discount_amount=discount,
            net_amount=gross - discount,
        )
        original = prepared_result_line(source, id=uid(14), position=1)
        parts = [
            build_refund_quote(original, quantity=1, allocation_offset=index)
            for index in range(count)
        ]
        assert all(part.gross_amount == price and part.discount_amount >= 0 for part in parts)
        assert sum(part.net_amount for part in parts) == original.net_amount
        assert sum(part.discount_amount for part in parts) == original.discount_amount
        assert sum(part.gross_amount for part in parts) == original.gross_amount
        for index, part in enumerate(parts):
            receipt_line = build_refund_receipt_line(part, name="Таблетки")
            assert receipt_line.gross_amount == receipt_line.original_retail_unit_price
            grouped = build_refund_quote(original, quantity=count - index, allocation_offset=index)
            assert grouped.net_amount == sum(p.net_amount for p in parts[index:])
            assert grouped.discount_amount == sum(p.discount_amount for p in parts[index:])
