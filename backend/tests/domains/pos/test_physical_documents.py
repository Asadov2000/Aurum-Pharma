"""Physical checkout snapshots and customer receipts remain distinct from v2."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.catalog.unit_sales import MAX_BASE_UNITS
from app.domains.inventory.unit_stock_v2 import UnitPackagingV2
from app.domains.pos.physical_documents import (
    PhysicalCheckoutResultV3,
    PhysicalReceiptDocumentV3,
    PhysicalReceiptLineV3,
    PhysicalResultLineV3,
    PhysicalRetailLineV3,
    build_physical_receipt,
    canonical_physical_hash,
    canonical_physical_json,
)
from app.domains.pos.unit_documents import ReceiptDocumentV2, canonical_json_v2


def packaging(**changes: object) -> dict[str, object]:
    return {
        "version": 2,
        "unit": "tablet",
        "units_per_pack": 30,
        "units_per_blister": 10,
        "dispense_step": 1,
        **changes,
    }


def line(**changes: object) -> dict[str, object]:
    return {
        "name": "Лекарство 500 мг",
        "packaging": packaging(),
        "sale_format": "blister",
        "quantity": 2,
        "base_units": 20,
        "original_retail_unit_price": "12.50",
        "gross_amount": "25.00",
        "discount_amount": "1.50",
        "net_amount": "23.50",
        **changes,
    }


def result_payload(**changes: object) -> dict[str, object]:
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
    return {
        **scope,
        "schema_version": 3,
        "document_kind": "physical_checkout",
        "created_at": "2026-10-07T10:00:00+05:00",
        "completed_at": "2026-10-07T10:01:00+05:00",
        "receipt_number": "A-42",
        "receipt_sequence": 42,
        "is_test": True,
        "lines": [line(id=UUID(int=20), position=1, catalog_id=UUID(int=21))],
        "payments": [{"id": UUID(int=22), "method": "cash", "amount": "23.50"}],
        "total_amount": "23.50",
        **changes,
    }


@pytest.mark.parametrize("sale_format,base_units", [("pack", 60), ("blister", 20), ("unit", 2)])
def test_display_quantity_and_physical_units_are_separate(
    sale_format: str, base_units: int
) -> None:
    value = PhysicalRetailLineV3.model_validate(
        line(sale_format=sale_format, base_units=base_units)
    )
    assert value.quantity == 2
    assert value.base_units == base_units
    assert value.net_amount == Decimal("23.50")


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
def test_receipt_labels_each_unit(unit: str, label: str) -> None:
    result = PhysicalCheckoutResultV3.model_validate(
        result_payload(
            lines=[
                line(
                    id=UUID(int=20),
                    position=1,
                    catalog_id=UUID(int=21),
                    sale_format="unit",
                    base_units=2,
                    packaging=packaging(unit=unit),
                )
            ]
        )
    )
    assert build_physical_receipt(result).lines[0].unit_label == label


@pytest.mark.parametrize(
    "sale_format,label,units", [("pack", "уп.", 60), ("blister", "блист.", 20)]
)
def test_receipt_labels_packages(sale_format: str, label: str, units: int) -> None:
    result = PhysicalCheckoutResultV3.model_validate(
        result_payload(
            lines=[
                line(
                    id=UUID(int=20),
                    position=1,
                    catalog_id=UUID(int=21),
                    sale_format=sale_format,
                    base_units=units,
                )
            ]
        )
    )
    assert build_physical_receipt(result).lines[0].unit_label == label


@pytest.mark.parametrize(
    "changes",
    [
        {"quantity": True},
        {"quantity": 2.0},
        {"quantity": 0},
        {"quantity": -1},
        {"base_units": 19},
        {"sale_format": "legacy_pack"},
        {"inventory_kind": "prepared_unit"},
        {"original_retail_unit_price": 12.5},
        {"gross_amount": True},
        {"discount_amount": "0.001"},
        {"net_amount": "NaN"},
        {"gross_amount": "Infinity"},
        {"gross_amount": "24.00"},
        {"net_amount": "0.00", "discount_amount": "25.00"},
        {"money_version": "1"},
        {"currency": "USD"},
        {"name": "   "},
        {"name": "药\n名"},
        {"name": "a\x00b"},
        {"name": "x" * 501},
    ],
)
def test_invalid_retail_facts_are_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PhysicalRetailLineV3.model_validate(line(**changes))


def test_unit_step_and_unknown_blister_composition() -> None:
    with pytest.raises(ValidationError):
        PhysicalRetailLineV3.model_validate(
            line(
                sale_format="unit",
                base_units=2,
                packaging=packaging(dispense_step=5),
            )
        )
    with pytest.raises(ValidationError):
        PhysicalRetailLineV3.model_validate(line(packaging=packaging(units_per_blister=None)))
    valid = PhysicalRetailLineV3.model_validate(
        line(
            sale_format="unit",
            base_units=2,
            packaging=packaging(units_per_blister=None),
        )
    )
    assert valid.packaging.units_per_blister is None


@pytest.mark.parametrize("field", ["version", "units_per_blister"])
def test_packaging_requires_explicit_historical_fields(field: str) -> None:
    snapshot = packaging()
    snapshot.pop(field)
    with pytest.raises(ValidationError):
        PhysicalRetailLineV3.model_validate(line(packaging=snapshot))


def test_constructed_packaging_cannot_bypass_validation() -> None:
    snapshot = UnitPackagingV2.model_construct(**packaging(units_per_blister=7))
    with pytest.raises(ValidationError):
        PhysicalRetailLineV3.model_validate(line(packaging=snapshot))


def test_quantity_and_document_collection_limits() -> None:
    with pytest.raises(ValidationError):
        PhysicalRetailLineV3.model_validate(line(quantity=MAX_BASE_UNITS + 1))
    for changes in (
        {
            "lines": [
                line(id=UUID(int=20 + index), position=index + 1, catalog_id=UUID(int=21))
                for index in range(201)
            ]
        },
        {
            "payments": [
                {"id": UUID(int=22 + index), "method": "cash", "amount": "1.00"}
                for index in range(11)
            ]
        },
    ):
        with pytest.raises(ValidationError):
            PhysicalCheckoutResultV3.model_validate(result_payload(**changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": "3"},
        {"schema_version": True},
        {"schema_version": 2},
        {"document_kind": "checkout"},
        {"total_amount": "23.49"},
        {"lines": []},
        {"payments": []},
        {"completed_at": "2026-10-07T10:01:00"},
        {"completed_at": "2026-10-07T09:59:00+05:00"},
    ],
)
def test_result_identity_and_balance_constraints(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PhysicalCheckoutResultV3.model_validate(result_payload(**changes))


@pytest.mark.parametrize(
    "positions,ids",
    [([1, 1], [20, 23]), ([1, 3], [20, 23]), ([2, 1], [20, 23]), ([1, 2], [20, 20])],
)
def test_result_positions_are_dense_ordered_and_ids_unique(
    positions: list[int], ids: list[int]
) -> None:
    lines = [
        line(id=UUID(int=identifier), position=position, catalog_id=UUID(int=21))
        for position, identifier in zip(positions, ids, strict=True)
    ]
    with pytest.raises(ValidationError):
        PhysicalCheckoutResultV3.model_validate(
            result_payload(
                lines=lines,
                total_amount="47.00",
                payments=[{"id": UUID(int=22), "method": "cash", "amount": "47.00"}],
            )
        )


def test_duplicate_payment_and_payment_imbalance_are_rejected() -> None:
    for payments in (
        [{"id": UUID(int=22), "method": "cash", "amount": "20.00"}],
        [
            {"id": UUID(int=22), "method": "cash", "amount": "20.00"},
            {"id": UUID(int=22), "method": "card", "amount": "3.50"},
        ],
    ):
        with pytest.raises(ValidationError):
            PhysicalCheckoutResultV3.model_validate(result_payload(payments=payments))


def test_receipt_contains_only_historical_display_data() -> None:
    result = PhysicalCheckoutResultV3.model_validate(result_payload())
    receipt = build_physical_receipt(result)
    encoded = canonical_physical_json(receipt)
    assert receipt.lines[0].name == result.lines[0].name
    assert receipt.lines[0].net_amount == result.lines[0].net_amount
    assert receipt.completed_at == datetime(2026, 10, 7, 5, 1, tzinfo=UTC)
    assert receipt.payments[0].amount == result.payments[0].amount
    for forbidden in ("tenant_id", "catalog_id", "stock_command_id", "cost", "purchase"):
        assert forbidden not in encoded
    assert str(result.lines[0].id) not in encoded
    assert canonical_physical_hash(receipt) == sha256(encoded.encode("utf-8")).hexdigest()
    assert build_physical_receipt(result) == receipt


def test_snapshots_and_receipts_are_frozen() -> None:
    result = PhysicalCheckoutResultV3.model_validate(result_payload())
    receipt = build_physical_receipt(result)
    with pytest.raises(ValidationError):
        result.total_amount = Decimal("1.00")
    with pytest.raises(ValidationError):
        result.lines[0].name = "Новое название"
    with pytest.raises(ValidationError):
        receipt.lines[0].name = "Новое название"
    assert build_physical_receipt(result).lines[0].name == "Лекарство 500 мг"


def test_receipt_cannot_forge_unit_label_or_unbalanced_total() -> None:
    receipt = build_physical_receipt(PhysicalCheckoutResultV3.model_validate(result_payload()))
    bad_line = receipt.lines[0].model_dump()
    bad_line["unit_label"] = "табл."
    with pytest.raises(ValidationError):
        PhysicalReceiptLineV3.model_validate(bad_line)
    payload = receipt.model_dump()
    payload["total_amount"] = "23.49"
    with pytest.raises(ValidationError):
        PhysicalReceiptDocumentV3.model_validate(payload)


def test_canonicalization_revalidates_constructed_documents() -> None:
    result = PhysicalCheckoutResultV3.model_validate(result_payload())
    forged = result.model_copy(update={"total_amount": Decimal("1.00")})
    with pytest.raises(ValidationError):
        canonical_physical_json(forged)
    bad_line = PhysicalResultLineV3.model_construct(
        **line(
            id=UUID(int=20),
            position=1,
            catalog_id=UUID(int=21),
            base_units=19,
        )
    )
    forged = result.model_copy(update={"lines": (bad_line,)})
    with pytest.raises(ValidationError):
        build_physical_receipt(forged)


def test_v2_and_physical_v3_canonical_domains_are_separate() -> None:
    receipt = build_physical_receipt(PhysicalCheckoutResultV3.model_validate(result_payload()))
    with pytest.raises((TypeError, ValueError)):
        canonical_json_v2(receipt)
    old = ReceiptDocumentV2.model_validate(
        {
            "schema_version": 2,
            "receipt_number": "A-42",
            "receipt_sequence": 42,
            "completed_at": "2026-10-07T05:01:00Z",
            "is_test": True,
            "lines": [
                {
                    "position": 1,
                    "name": "Лекарство",
                    "inventory_kind": "prepared_unit",
                    "sale_format": "unit",
                    "quantity": 2,
                    "unit_label": "табл.",
                    "original_retail_unit_price": "12.50",
                    "gross_amount": "25.00",
                    "discount_amount": "1.50",
                    "net_amount": "23.50",
                }
            ],
            "payments": [{"method": "cash", "amount": "23.50"}],
            "total_amount": "23.50",
        }
    )
    with pytest.raises((TypeError, ValueError)):
        canonical_physical_json(old)


def test_exact_decimal_arithmetic_and_utc_do_not_depend_on_ambient_settings() -> None:
    baseline = PhysicalCheckoutResultV3.model_validate(result_payload())
    with localcontext() as context:
        context.prec = 2
        result = PhysicalCheckoutResultV3.model_validate(result_payload())
        receipt = build_physical_receipt(result)
        assert canonical_physical_json(result) == canonical_physical_json(baseline)
        assert receipt.total_amount == Decimal("23.50")
    payload = result_payload(
        completed_at=datetime(2026, 10, 7, 10, 1, tzinfo=timezone(timedelta(hours=5)))
    )
    assert PhysicalCheckoutResultV3.model_validate(payload).completed_at == baseline.completed_at


def test_saved_canonical_documents_roundtrip_and_normalize_signed_zero() -> None:
    result = PhysicalCheckoutResultV3.model_validate(
        result_payload(
            lines=[
                line(
                    id=UUID(int=20),
                    position=1,
                    catalog_id=UUID(int=21),
                    discount_amount="-0.00",
                    net_amount="25.00",
                )
            ],
            payments=[{"id": UUID(int=22), "method": "cash", "amount": "25.00"}],
            total_amount="25.00",
        )
    )
    receipt = build_physical_receipt(result)
    for document in (result, receipt):
        encoded = canonical_physical_json(document)
        restored = type(document).model_validate_json(encoded)
        assert canonical_physical_json(restored) == encoded
        assert canonical_physical_hash(restored) == canonical_physical_hash(document)
        assert '"discount_amount":"0.00"' in encoded
        assert "-0.00" not in encoded


@pytest.mark.parametrize("changes", [{"version": True}, {"units_per_blister": 10.0}])
def test_packaging_does_not_coerce_physical_snapshot(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PhysicalRetailLineV3.model_validate(line(packaging=packaging(**changes)))


def test_copied_packaging_is_revalidated_at_document_boundary() -> None:
    snapshot = UnitPackagingV2.model_validate(packaging()).model_copy(update={"dispense_step": 7})
    with pytest.raises(ValidationError):
        PhysicalRetailLineV3.model_validate(line(packaging=snapshot))


@pytest.mark.parametrize("field", ["cost_amount", "prescription"])
def test_sensitive_extra_fields_cannot_be_serialized(field: str) -> None:
    result = PhysicalCheckoutResultV3.model_validate(result_payload())
    receipt = build_physical_receipt(result)
    for document in (result, receipt):
        payload = document.model_dump()
        payload[field] = "not customer display data"
        with pytest.raises(ValidationError):
            type(document).model_validate(payload)
        line_payload = document.lines[0].model_dump()
        line_payload[field] = "not customer display data"
        with pytest.raises(ValidationError):
            type(document.lines[0]).model_validate(line_payload)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": "3"},
        {"receipt_number": "A\x7f42"},
        {"completed_at": "2026-10-07T05:01:00"},
        {"completed_at": 1791349260},
    ],
)
def test_receipt_metadata_rejects_ambiguous_or_unprintable_values(
    changes: dict[str, object],
) -> None:
    receipt = build_physical_receipt(PhysicalCheckoutResultV3.model_validate(result_payload()))
    with pytest.raises(ValidationError):
        PhysicalReceiptDocumentV3.model_validate({**receipt.model_dump(), **changes})
