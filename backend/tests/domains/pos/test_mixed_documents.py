"""Mixed historical documents preserve both inventory semantics without posting."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_UP, Decimal, localcontext
from hashlib import sha256
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.pos.mixed_documents import (
    LegacyReceiptLineV4,
    LegacyResultLineV4,
    MixedCheckoutResultV4,
    MixedReceiptDocumentV4,
    build_mixed_receipt,
    canonical_mixed_hash,
    canonical_mixed_json,
)
from app.domains.pos.physical_documents import (
    PhysicalCheckoutResultV3,
    PhysicalResultLineV3,
    build_physical_receipt,
    canonical_physical_json,
)
from app.domains.pos.unit_contracts import CheckoutResultV2
from app.domains.pos.unit_documents import build_receipt, canonical_json_v2
from tests.domains.pos.test_physical_documents import line as physical_line
from tests.domains.pos.test_physical_documents import result_payload


def line(**changes: object) -> dict[str, object]:
    return {"inventory_kind": "physical_unit", **physical_line(**changes)}


def legacy(**changes: object) -> dict[str, object]:
    return {
        "inventory_kind": "legacy_pack",
        "id": UUID(int=40),
        "position": 1,
        "catalog_id": UUID(int=41),
        "batch_id": UUID(int=42),
        "name": "Сохранённое обычное лекарство",
        "quantity": "0.001",
        "original_retail_unit_price": "25.00",
        "gross_amount": "0.02",
        "discount_amount": "0.00",
        "net_amount": "0.02",
        **changes,
    }


def mixed_payload(**changes: object) -> dict[str, object]:
    payload = result_payload(
        schema_version=4,
        document_kind="mixed_checkout",
        lines=[legacy(), line(id=UUID(int=20), position=2, catalog_id=UUID(int=21))],
        payments=[{"id": UUID(int=22), "method": "cash", "amount": "23.52"}],
        total_amount="23.52",
    )
    payload.update(changes)
    return payload


@pytest.mark.parametrize(
    "sale_format,base_units,label",
    [("pack", 60, "уп."), ("blister", 20, "блист."), ("unit", 2, "табл.")],
)
def test_mixed_receipt_retains_fractional_packs_and_each_physical_format(
    sale_format: str, base_units: int, label: str
) -> None:
    payload = mixed_payload()
    payload["lines"] = [
        legacy(),
        line(
            id=UUID(int=20),
            position=2,
            catalog_id=UUID(int=21),
            sale_format=sale_format,
            base_units=base_units,
        ),
    ]
    result = MixedCheckoutResultV4.model_validate(payload)
    receipt = build_mixed_receipt(result)
    assert result.lines[0].id == UUID(int=40)
    assert result.lines[0].quantity == Decimal("0.001")
    assert receipt.lines[0].quantity == Decimal("0.001")
    assert receipt.lines[0].sale_format == "pack"
    assert receipt.lines[0].unit_label == "уп."
    assert receipt.lines[1].quantity == 2
    assert receipt.lines[1].unit_label == label
    assert receipt.total_amount == Decimal("23.52")
    assert tuple(row.position for row in receipt.lines) == (1, 2)


@pytest.mark.parametrize("price,gross", [("25.00", "0.02"), ("15.00", "0.02"), ("5.00", "0.00")])
def test_legacy_rounding_retains_half_even_without_repricing(price: str, gross: str) -> None:
    value = LegacyResultLineV4.model_validate(
        legacy(original_retail_unit_price=price, gross_amount=gross, net_amount=gross)
    )
    assert value.gross_amount == Decimal(gross)
    assert value.quantity == Decimal("0.001")


def test_fully_discounted_legacy_line_remains_valid_in_positive_mixed_sale() -> None:
    payload = mixed_payload()
    payload["lines"] = [
        legacy(discount_amount="0.02", net_amount="0.00"),
        line(id=UUID(int=20), position=2, catalog_id=UUID(int=21)),
    ]
    payload["total_amount"] = "23.50"
    payload["payments"] = [{"id": UUID(int=22), "method": "cash", "amount": "23.50"}]
    receipt = build_mixed_receipt(MixedCheckoutResultV4.model_validate(payload))
    assert receipt.lines[0].net_amount == Decimal("0.00")
    assert receipt.lines[0].discount_amount == Decimal("0.02")


@pytest.mark.parametrize(
    "changes",
    [
        {"quantity": True},
        {"quantity": 0.001},
        {"quantity": "0.0001"},
        {"quantity": "0.000"},
        {"quantity": "100000000000.000"},
        {"gross_amount": "0.03"},
        {"discount_amount": "0.001"},
        {"net_amount": "NaN"},
        {"original_retail_unit_price": float("inf")},
        {"name": " "},
        {"name": "a\x7fb"},
        {"name": "a\n药"},
        {"name": "x" * 501},
        {"inventory_kind": "prepared_unit"},
        {"currency": "USD"},
        {"money_version": True},
        {"packaging": {"version": 2}},
        {"cost_amount": "1.00"},
    ],
)
def test_invalid_legacy_facts_never_coerce_into_physical_or_prepared(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        LegacyResultLineV4.model_validate(legacy(**changes))


@pytest.mark.parametrize("kind", ["legacy_only", "physical_only", "prepared", "unknown", "empty"])
def test_result_requires_both_explicit_inventory_kinds(kind: str) -> None:
    payload = mixed_payload()
    if kind == "legacy_only":
        payload["lines"] = [legacy(), legacy(id=UUID(int=43), position=2)]
        payload["total_amount"] = "0.04"
        payload["payments"] = [{"id": UUID(int=22), "method": "cash", "amount": "0.04"}]
    elif kind == "physical_only":
        payload["lines"] = [
            line(id=UUID(int=20), position=1, catalog_id=UUID(int=21)),
            line(id=UUID(int=23), position=2, catalog_id=UUID(int=21)),
        ]
        payload["total_amount"] = "47.00"
        payload["payments"] = [{"id": UUID(int=22), "method": "cash", "amount": "47.00"}]
    elif kind == "empty":
        payload["lines"] = []
    else:
        payload["lines"] = [legacy(inventory_kind="prepared_unit" if kind == "prepared" else kind)]
    with pytest.raises(ValidationError):
        MixedCheckoutResultV4.model_validate(payload)


@pytest.mark.parametrize("change", ["same_id", "gap", "reverse", "duplicate_position"])
def test_ids_are_unique_across_inventory_kinds_and_positions_are_dense(change: str) -> None:
    payload = mixed_payload()
    first = legacy()
    second = line(id=UUID(int=20), position=2, catalog_id=UUID(int=21))
    if change == "same_id":
        second["id"] = first["id"]
    elif change == "gap":
        second["position"] = 3
    elif change == "reverse":
        first["position"], second["position"] = 2, 1
    else:
        second["position"] = 1
    payload["lines"] = [first, second]
    with pytest.raises(ValidationError):
        MixedCheckoutResultV4.model_validate(payload)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"schema_version": "4"},
        {"schema_version": 3},
        {"document_kind": "physical_checkout"},
        {"stock_command_id": "not-a-uuid"},
        {"total_amount": "23.51"},
        {"payments": []},
        {"currency": "USD"},
        {"completed_at": "2026-10-07T10:01:00"},
        {"completed_at": 1791349260},
        {"completed_at": "2026-10-07T09:59:00+05:00"},
        {"receipt_number": "A\x7f42"},
    ],
)
def test_result_scope_version_and_balance_are_strict(changes: dict[str, object]) -> None:
    payload = mixed_payload()
    payload.update(changes)
    with pytest.raises(ValidationError):
        MixedCheckoutResultV4.model_validate(payload)


@pytest.mark.parametrize("duplicate", [False, True])
def test_payments_reject_imbalance_and_duplicate_identifiers(duplicate: bool) -> None:
    payload = mixed_payload()
    payload["payments"] = [
        {"id": UUID(int=22), "method": "cash", "amount": "20.00"},
        {
            "id": UUID(int=22 if duplicate else 23),
            "method": "card",
            "amount": "3.52" if duplicate else "3.51",
        },
    ]
    with pytest.raises(ValidationError):
        MixedCheckoutResultV4.model_validate(payload)


@pytest.mark.parametrize("count", [200, 201])
def test_combined_line_limit_applies_to_both_inventory_kinds_together(count: int) -> None:
    payload = mixed_payload()
    payload["lines"] = [
        legacy(id=UUID(int=1000 + index), position=index + 1) for index in range(count - 1)
    ] + [line(id=UUID(int=20), position=count, catalog_id=UUID(int=21))]
    total = format(Decimal("23.50") + Decimal("0.02") * (count - 1), ".2f")
    payload["total_amount"] = total
    payload["payments"] = [{"id": UUID(int=22), "method": "cash", "amount": total}]
    if count == 200:
        result = MixedCheckoutResultV4.model_validate(payload)
        assert len(build_mixed_receipt(result).lines) == 200
    else:
        with pytest.raises(ValidationError):
            MixedCheckoutResultV4.model_validate(payload)


@pytest.mark.parametrize("count", [10, 11])
def test_payment_collection_limits_are_preserved(count: int) -> None:
    payload = mixed_payload()
    payload["payments"] = [
        {"id": UUID(int=100 + index), "method": "cash", "amount": "1.00"}
        for index in range(count - 1)
    ] + [
        {
            "id": UUID(int=200),
            "method": "qr",
            "amount": format(Decimal("23.52") - (count - 1), ".2f"),
        }
    ]
    if count == 10:
        receipt = build_mixed_receipt(MixedCheckoutResultV4.model_validate(payload))
        assert len(receipt.payments) == 10
    else:
        with pytest.raises(ValidationError):
            MixedCheckoutResultV4.model_validate(payload)


def test_receipt_uses_only_original_names_and_whitelisted_customer_facts() -> None:
    payload = mixed_payload()
    result = MixedCheckoutResultV4.model_validate(payload)
    receipt = build_mixed_receipt(result)
    encoded = canonical_mixed_json(receipt)
    assert receipt.lines[0].name == "Сохранённое обычное лекарство"
    assert receipt.lines[1].name == "Лекарство 500 мг"
    assert receipt.completed_at == datetime(2026, 10, 7, 5, 1, tzinfo=UTC)
    assert '"quantity":"0.001"' in encoded
    fields = (
        "tenant_id",
        "catalog_id",
        "batch_id",
        "stock_command_id",
        "cost",
        "purchase",
        "prescription",
    )
    for field in fields:
        assert field not in encoded
    for identifier in (UUID(int=40), UUID(int=42), UUID(int=20), UUID(int=22)):
        assert str(identifier) not in encoded
    assert build_mixed_receipt(result) == receipt
    assert canonical_mixed_hash(receipt) == sha256(encoded.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    "field", ["batch_id", "catalog_id", "stock_command_id", "operation_id", "event_id"]
)
def test_original_provenance_changes_result_hash_without_leaking_to_receipt(field: str) -> None:
    result = MixedCheckoutResultV4.model_validate(mixed_payload())
    payload = result.model_dump()
    if field in {"batch_id", "catalog_id"}:
        payload["lines"][0][field] = UUID(int=900)
    else:
        payload[field] = UUID(int=900)
    changed = MixedCheckoutResultV4.model_validate(payload)
    assert canonical_mixed_hash(changed) != canonical_mixed_hash(result)
    assert canonical_mixed_json(build_mixed_receipt(changed)) == canonical_mixed_json(
        build_mixed_receipt(result)
    )


def test_documents_are_recursively_frozen_and_independent_of_input_mutation() -> None:
    source = legacy()
    payload = mixed_payload()
    payload["lines"] = [source, line(id=UUID(int=20), position=2, catalog_id=UUID(int=21))]
    result = MixedCheckoutResultV4.model_validate(payload)
    receipt = build_mixed_receipt(result)
    source["name"] = "Новое имя"
    with pytest.raises(ValidationError):
        result.total_amount = Decimal("1.00")
    with pytest.raises(ValidationError):
        result.lines[0].name = "Новое имя"
    with pytest.raises(ValidationError):
        receipt.lines[0].name = "Новое имя"
    assert receipt.lines[0].name == "Сохранённое обычное лекарство"
    assert build_mixed_receipt(result) == receipt


@pytest.mark.parametrize("forgery", ["total", "legacy_quantity", "physical_units"])
def test_builder_and_canonicalizer_revalidate_copied_and_constructed_models(forgery: str) -> None:
    result = MixedCheckoutResultV4.model_validate(mixed_payload())
    if forgery == "total":
        forged = result.model_copy(update={"total_amount": Decimal("1.00")})
    else:
        lines = list(result.lines)
        index = 0 if forgery == "legacy_quantity" else 1
        payload = lines[index].model_dump()
        field = "quantity" if index == 0 else "base_units"
        payload[field] = Decimal("2.000") if index == 0 else 19
        cls = LegacyResultLineV4 if index == 0 else PhysicalResultLineV3
        lines[index] = cls.model_construct(**payload)
        forged = result.model_copy(update={"lines": tuple(lines)})
    with pytest.raises(ValidationError):
        build_mixed_receipt(forged)
    with pytest.raises(ValidationError):
        canonical_mixed_json(forged)


@pytest.mark.parametrize(
    "changes", [{"sale_format": "blister"}, {"unit_label": "табл."}, {"name": "a\x7fb"}]
)
def test_legacy_receipt_cannot_forge_format_or_printable_name(changes: dict[str, object]) -> None:
    receipt = build_mixed_receipt(MixedCheckoutResultV4.model_validate(mixed_payload()))
    payload = receipt.lines[0].model_dump()
    payload.update(changes)
    with pytest.raises(ValidationError):
        LegacyReceiptLineV4.model_validate(payload)


def test_receipt_cannot_drop_an_inventory_kind_or_change_totals() -> None:
    receipt = build_mixed_receipt(MixedCheckoutResultV4.model_validate(mixed_payload()))
    first = receipt.lines[0].model_dump()
    second = {**first, "position": 2}
    changes = (
        {"total_amount": "23.51"},
        {
            "lines": [first, second],
            "total_amount": "0.04",
            "payments": [{"method": "cash", "amount": "0.04"}],
        },
        {"schema_version": "4"},
    )
    for change in changes:
        payload = receipt.model_dump()
        payload.update(change)
        with pytest.raises(ValidationError):
            MixedReceiptDocumentV4.model_validate(payload)


def test_canonical_roundtrip_and_arithmetic_ignore_ambient_decimal_settings() -> None:
    baseline = MixedCheckoutResultV4.model_validate(mixed_payload())
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_UP
        result = MixedCheckoutResultV4.model_validate(mixed_payload())
        assert canonical_mixed_json(result) == canonical_mixed_json(baseline)
        for document in (result, build_mixed_receipt(result)):
            encoded = canonical_mixed_json(document)
            restored = type(document).model_validate_json(encoded)
            assert canonical_mixed_json(restored) == encoded
            assert canonical_mixed_hash(restored) == canonical_mixed_hash(document)
    payload = mixed_payload()
    payload["completed_at"] = datetime(2026, 10, 7, 10, 1, tzinfo=timezone(timedelta(hours=5)))
    changed = MixedCheckoutResultV4.model_validate(payload)
    assert canonical_mixed_json(changed) == canonical_mixed_json(baseline)


def test_signed_zero_and_equivalent_legacy_quantity_have_one_canonical_form() -> None:
    payload = mixed_payload()
    payload["lines"] = [
        legacy(quantity="0.0010", discount_amount="-0.00"),
        line(id=UUID(int=20), position=2, catalog_id=UUID(int=21)),
    ]
    result = MixedCheckoutResultV4.model_validate(payload)
    for document in (result, build_mixed_receipt(result)):
        encoded = canonical_mixed_json(document)
        assert '"discount_amount":"0.00"' in encoded
        assert '"quantity":"0.001"' in encoded
        assert "-0.00" not in encoded


@pytest.mark.parametrize("field", ["cost_amount", "purchase_price", "prescription"])
def test_sensitive_extras_are_rejected_at_document_and_line_boundaries(field: str) -> None:
    result = MixedCheckoutResultV4.model_validate(mixed_payload())
    for document in (result, build_mixed_receipt(result)):
        payload = document.model_dump()
        payload[field] = "forbidden"
        with pytest.raises(ValidationError):
            type(document).model_validate(payload)
        for row in document.lines:
            line_payload = row.model_dump()
            line_payload[field] = "forbidden"
            with pytest.raises(ValidationError):
                type(row).model_validate(line_payload)


def test_old_document_domains_remain_separate_from_mixed_v4() -> None:
    mixed = MixedCheckoutResultV4.model_validate(mixed_payload())
    mixed_receipt = build_mixed_receipt(mixed)
    physical = PhysicalCheckoutResultV3.model_validate(result_payload())
    old_payload = result_payload()
    old_payload.pop("stock_command_id")
    old_payload.pop("document_kind")
    old_payload.update(
        schema_version=2,
        lines=[{key: value for key, value in legacy().items() if key != "name"}],
        payments=[{"id": UUID(int=22), "method": "cash", "amount": "0.02"}],
        total_amount="0.02",
    )
    old = CheckoutResultV2.model_validate(old_payload)
    old_receipt = build_receipt(old, names={UUID(int=41): "Обычное лекарство"})
    for document in (physical, build_physical_receipt(physical), old, old_receipt):
        with pytest.raises((TypeError, ValueError)):
            canonical_mixed_json(document)
        with pytest.raises((TypeError, ValueError)):
            build_mixed_receipt(document)
    for document in (mixed, mixed_receipt):
        with pytest.raises((TypeError, ValueError)):
            canonical_physical_json(document)
        with pytest.raises((TypeError, ValueError)):
            canonical_json_v2(document)
    assert canonical_physical_json(physical) == canonical_physical_json(
        PhysicalCheckoutResultV3.model_validate_json(canonical_physical_json(physical))
    )
    assert canonical_json_v2(old_receipt) == canonical_json_v2(
        type(old_receipt).model_validate_json(canonical_json_v2(old_receipt))
    )
