"""Deterministic typed projection for Cloud-to-Edge read snapshots."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from app.domains.sync.read_snapshot_projection import (
    CatalogProductRecord,
    FoundationRecord,
    InventoryBatchRecord,
    SupplierRecord,
    build_component_chunks,
    parse_component_items,
)

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
BRANCH_ID = UUID("10000000-0000-0000-0000-000000000002")
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _foundation() -> FoundationRecord:
    return FoundationRecord(
        source_fingerprint="f" * 64,
        tenant={
            "id": TENANT_ID,
            "name": "Aurum",
            "legal_name": None,
            "inn_or_tin": None,
            "registration_number": None,
            "legal_address": None,
            "status": "active",
            "drug_catalog_mode": "autonomous",
            "updated_at": NOW,
        },
        settings={
            "expiry_thresholds": {"yellow": 6, "orange": 3, "red": 1},
            "expired_sale_mode": "strict",
            "refund_reason_mode": "optional",
            "pos_payment_methods": ("cash",),
            "pos_mixed_payment_enabled": False,
            "draft_sale_lifetime_min": 30,
            "report_timezone": "Asia/Dushanbe",
            "prescription_warning_text": "Prescription required",
            "version": 1,
            "updated_at": NOW,
        },
        branch={
            "id": BRANCH_ID,
            "tenant_id": TENANT_ID,
            "name": "Center",
            "address": None,
            "branch_type": "pharmacy",
            "license_number": None,
            "license_expires_at": None,
            "working_hours": None,
            "receipt_header": None,
            "is_active": True,
            "updated_at": NOW,
        },
        registers=(),
    )


def _catalog(item_id: int) -> CatalogProductRecord:
    return CatalogProductRecord(
        id=UUID(f"10000000-0000-0000-0000-{item_id:012d}"),
        tenant_id=TENANT_ID,
        brand_name=f"Product {item_id}",
        inn=None,
        manufacturer=None,
        form=None,
        dosage=None,
        pack_size=None,
        atx_code=None,
        dispensing_type="otc",
        storage_type="normal",
        category=None,
        base_price=Decimal("12.50"),
        currency="TJS",
        image_version=None,
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
        deleted_at=None,
        barcodes=(),
    )


def test_projection_is_sorted_chunked_and_round_trips_strict_types() -> None:
    foundation = _foundation()
    supplier = SupplierRecord(
        id=UUID("10000000-0000-0000-0000-000000000010"),
        tenant_id=TENANT_ID,
        name="Supplier",
        legal_name=None,
        inn_or_tin=None,
        contact_person=None,
        phone=None,
        email=None,
        address=None,
        notes=None,
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
    )
    batch = InventoryBatchRecord(
        id=UUID("10000000-0000-0000-0000-000000000020"),
        tenant_id=TENANT_ID,
        branch_id=BRANCH_ID,
        catalog_id=_catalog(1).id,
        batch_number="LOT-1",
        manufactured_at=None,
        expires_at=date(2027, 9, 20),
        purchase_price=Decimal("10.00"),
        sale_price=Decimal("12.50"),
        currency="TJS",
        qty_initial=Decimal("5.000"),
        qty_remaining=Decimal("4.000"),
        is_blocked=False,
        block_reason=None,
        blocked_at=None,
        created_at=NOW,
        updated_at=NOW,
        branch_name="Center",
        catalog_name="Product 1",
        catalog_form=None,
        catalog_dosage=None,
        catalog_pack_size=None,
    )

    chunks = build_component_chunks(
        foundation=foundation,
        catalog=(_catalog(2), _catalog(1)),
        suppliers=(supplier,),
        inventory=(batch,),
        chunk_size=2,
    )

    assert [item["id"] for chunk in chunks["catalog"] for item in chunk] == [
        str(_catalog(1).id),
        str(_catalog(2).id),
        str(supplier.id),
    ]
    assert [len(chunk) for chunk in chunks["catalog"]] == [2, 1]
    parsed = parse_component_items(
        "inventory", [item for chunk in chunks["inventory"] for item in chunk]
    )
    assert parsed == (batch,)


def test_empty_components_keep_verifiable_chunk_zero() -> None:
    foundation = _foundation()

    chunks = build_component_chunks(
        foundation=foundation,
        catalog=(),
        suppliers=(),
        inventory=(),
        chunk_size=50,
    )

    assert chunks["catalog"] == ((),)
    assert chunks["inventory"] == ((),)
