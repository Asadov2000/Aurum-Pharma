"""Local Edge APIs query the atomically active indexed projection."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest

from app.core.errors import ServiceUnavailableError
from app.domains.sync.credentials import EdgeCredential
from app.domains.sync.models import SyncReadSnapshot
from app.domains.sync.read_model import LocalReadModel
from app.domains.sync.read_snapshot_projection import (
    CatalogProductRecord,
    FoundationRecord,
    InventoryBatchRecord,
    SupplierRecord,
)
from app.domains.sync.repository import InventoryProjectionSummary, SyncEdgeRepository

TENANT_ID = UUID("20000000-0000-0000-0000-000000000001")
BRANCH_ID = UUID("20000000-0000-0000-0000-000000000002")
SNAPSHOT_ID = UUID("20000000-0000-0000-0000-000000000003")
NOW = datetime.now(UTC)
CREDENTIAL = EdgeCredential(kid=UUID("20000000-0000-0000-0000-000000000099"), secret="a" * 64)


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


def _product(*, tenant_id: UUID = TENANT_ID) -> CatalogProductRecord:
    return CatalogProductRecord(
        id=UUID("20000000-0000-0000-0000-000000000010"),
        tenant_id=tenant_id,
        brand_name="Парацетамол",
        inn="paracetamol",
        manufacturer="Aurum",
        form="tablet",
        dosage="500 mg",
        pack_size="10",
        atx_code=None,
        dispensing_type="otc",
        storage_type="normal",
        category="pain",
        base_price=Decimal("5.00"),
        currency="TJS",
        image_version=None,
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
        deleted_at=None,
        barcodes=(
            {
                "id": UUID("20000000-0000-0000-0000-000000000011"),
                "code": "12345678",
                "code_type": "ean8",
            },
        ),
    )


def _batch() -> InventoryBatchRecord:
    return InventoryBatchRecord(
        id=UUID("20000000-0000-0000-0000-000000000020"),
        tenant_id=TENANT_ID,
        branch_id=BRANCH_ID,
        catalog_id=_product().id,
        batch_number="LOT-1",
        manufactured_at=None,
        expires_at=date.today() + timedelta(days=400),
        purchase_price=Decimal("3.00"),
        sale_price=Decimal("5.00"),
        currency="TJS",
        qty_initial=Decimal("10"),
        qty_remaining=Decimal("8"),
        is_blocked=False,
        block_reason=None,
        blocked_at=None,
        created_at=NOW,
        updated_at=NOW,
        branch_name="Center",
        catalog_name="Парацетамол",
        catalog_form="tablet",
        catalog_dosage="500 mg",
        catalog_pack_size="10",
    )


class _FakeRepo:
    def __init__(
        self,
        *,
        active: object | None,
        product: CatalogProductRecord | None = None,
        batch: InventoryBatchRecord | None = None,
    ) -> None:
        self.active = active
        self.product = product or _product()
        self.batch = batch or _batch()

    async def get_active_read_projection(self, **_kwargs: object) -> object | None:
        return self.active

    async def list_read_catalog_projection(self, **_kwargs: object) -> tuple[list[object], int]:
        return [SimpleNamespace(payload=self.product.model_dump(mode="json"))], 1

    async def get_read_catalog_projection(self, **_kwargs: object) -> tuple[object, list[object]]:
        barcode = self.product.barcodes[0]
        return (
            SimpleNamespace(payload=self.product.model_dump(mode="json")),
            [
                SimpleNamespace(
                    barcode_id=barcode.id,
                    code=barcode.code,
                    code_type=barcode.code_type,
                )
            ],
        )

    async def list_read_supplier_projection(self, **_kwargs: object) -> tuple[list[object], int]:
        supplier = SupplierRecord(
            id=UUID("20000000-0000-0000-0000-000000000030"),
            tenant_id=TENANT_ID,
            name="Aurum Supplier",
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
        return [SimpleNamespace(payload=supplier.model_dump(mode="json"))], 1

    async def list_read_inventory_projection(
        self, **_kwargs: object
    ) -> tuple[list[object], int, InventoryProjectionSummary]:
        return (
            [SimpleNamespace(payload=self.batch.model_dump(mode="json"))],
            1,
            InventoryProjectionSummary(
                total_qty=Decimal("8"),
                purchase_value=Decimal("24"),
                sale_value=Decimal("40"),
                attention_count=0,
                expired_count=0,
                blocked_count=0,
            ),
        )


def _active(
    *,
    source_generated_at: datetime | None = None,
    confirmed_at: datetime | None = None,
    foundation_payload: dict[str, object] | None = None,
) -> tuple[object, object, datetime]:
    snapshot = SimpleNamespace(
        snapshot_id=SNAPSHOT_ID,
        signature="1" * 64,
        source_revision=4,
        source_generated_at=source_generated_at or NOW - timedelta(days=2),
    )
    projection = SimpleNamespace(
        foundation_payload=foundation_payload or _foundation().model_dump(mode="json")
    )
    return snapshot, projection, confirmed_at or NOW - timedelta(days=2)


async def _trust_source(_snapshot: SyncReadSnapshot, _credential: str) -> None:
    return None


def _service(repo: _FakeRepo) -> LocalReadModel:
    return LocalReadModel(
        cast(SyncEdgeRepository, repo),
        source_verifier=_trust_source,
    )


@pytest.fixture(autouse=True)
def _edge_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(
        EDGE_SYNC_CREDENTIAL=SimpleNamespace(get_secret_value=lambda: CREDENTIAL.token),
        EDGE_READ_SNAPSHOT_STALE_SECONDS=86400,
        EDGE_SYNC_MAX_CLOCK_SKEW_SECONDS=300,
    )
    monkeypatch.setattr("app.domains.sync.read_model.get_settings", lambda: settings)


@pytest.mark.asyncio
async def test_catalog_barcode_and_inventory_use_indexed_projection_with_freshness() -> None:
    service = _service(_FakeRepo(active=_active()))
    catalog = await service.catalog(
        tenant_id=TENANT_ID, branch_id=BRANCH_ID, query="пара", page=1, page_size=20
    )
    details = await service.catalog_details(
        tenant_id=TENANT_ID, branch_id=BRANCH_ID, barcode="12345678"
    )
    inventory = await service.inventory(
        tenant_id=TENANT_ID,
        branch_id=BRANCH_ID,
        can_view_costs=False,
        show_empty=False,
        page=1,
        page_size=20,
    )
    assert catalog.metadata.is_stale is True
    assert catalog.items[0].brand_name == "Парацетамол"
    assert details.item.barcodes[0].code == "12345678"
    assert inventory.items[0].purchase_price is None
    assert inventory.summary.purchase_value is None
    assert inventory.summary.total_qty == Decimal("8")


@pytest.mark.asyncio
async def test_missing_active_projection_is_explicitly_unavailable() -> None:
    service = _service(_FakeRepo(active=None))
    with pytest.raises(ServiceUnavailableError, match="not installed"):
        await service.foundation(tenant_id=TENANT_ID, branch_id=BRANCH_ID)


@pytest.mark.asyncio
async def test_corrupted_foundation_projection_is_never_served() -> None:
    service = _service(_FakeRepo(active=_active(foundation_payload={"broken": True})))
    with pytest.raises(ServiceUnavailableError, match="corrupted"):
        await service.foundation(tenant_id=TENANT_ID, branch_id=BRANCH_ID)


@pytest.mark.asyncio
async def test_cross_tenant_catalog_projection_is_never_served() -> None:
    service = _service(_FakeRepo(active=_active(), product=_product(tenant_id=UUID(int=77))))
    with pytest.raises(ServiceUnavailableError, match="corrupted"):
        await service.catalog(
            tenant_id=TENANT_ID, branch_id=BRANCH_ID, query=None, page=1, page_size=20
        )


@pytest.mark.asyncio
async def test_snapshot_from_far_future_is_never_reported_as_fresh() -> None:
    service = _service(
        _FakeRepo(
            active=_active(
                source_generated_at=datetime.now(UTC) + timedelta(hours=1),
                confirmed_at=datetime.now(UTC),
            )
        )
    )
    with pytest.raises(ServiceUnavailableError, match="clock is invalid"):
        await service.foundation(tenant_id=TENANT_ID, branch_id=BRANCH_ID)


@pytest.mark.asyncio
async def test_recent_confirmation_keeps_unchanged_snapshot_fresh() -> None:
    service = _service(_FakeRepo(active=_active(confirmed_at=datetime.now(UTC))))
    result = await service.foundation(tenant_id=TENANT_ID, branch_id=BRANCH_ID)
    assert result.metadata.source_generated_at < datetime.now(UTC) - timedelta(days=1)
    assert result.metadata.last_confirmed_at > datetime.now(UTC) - timedelta(minutes=1)
    assert result.metadata.is_stale is False
