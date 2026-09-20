"""Local Edge read model serves only an atomically active typed snapshot."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest

from app.core.errors import ServiceUnavailableError
from app.domains.sync.credentials import EdgeCredential
from app.domains.sync.read_model import LocalReadModel
from app.domains.sync.read_snapshot import ReadSnapshotScope, build_manifest
from app.domains.sync.read_snapshot_projection import (
    CatalogProductRecord,
    FoundationRecord,
    InventoryBatchRecord,
    build_component_chunks,
)
from app.domains.sync.repository import SyncEdgeRepository

TENANT_ID = UUID("20000000-0000-0000-0000-000000000001")
BRANCH_ID = UUID("20000000-0000-0000-0000-000000000002")
SNAPSHOT_ID = UUID("20000000-0000-0000-0000-000000000003")
NOW = datetime.now(UTC)
CREDENTIAL = EdgeCredential(kid=UUID("20000000-0000-0000-0000-000000000099"), secret="a" * 64)


class _FakeRepo:
    def __init__(self, stored: object | None) -> None:
        self.stored = stored

    async def get_active_read_snapshot(self, **_kwargs: object) -> object | None:
        return self.stored


def _stored_snapshot(
    *,
    catalog_tenant_id: UUID = TENANT_ID,
    source_generated_at: datetime | None = None,
    confirmed_at: datetime | None = None,
) -> tuple[object, list[object], list[object], datetime]:
    product = CatalogProductRecord(
        id=UUID("20000000-0000-0000-0000-000000000010"),
        tenant_id=catalog_tenant_id,
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
                "id": "20000000-0000-0000-0000-000000000011",
                "code": "12345678",
                "code_type": "ean8",
            },
        ),
    )
    batch = InventoryBatchRecord(
        id=UUID("20000000-0000-0000-0000-000000000020"),
        tenant_id=TENANT_ID,
        branch_id=BRANCH_ID,
        catalog_id=product.id,
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
        catalog_name=product.brand_name,
        catalog_form=product.form,
        catalog_dosage=product.dosage,
        catalog_pack_size=product.pack_size,
    )
    components = build_component_chunks(
        foundation=FoundationRecord(
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
        ),
        catalog=(product,),
        suppliers=(),
        inventory=(batch,),
        chunk_size=50,
    )
    envelope, signed_chunks = build_manifest(
        ReadSnapshotScope(
            edge_node_id=UUID("20000000-0000-0000-0000-000000000098"),
            tenant_id=TENANT_ID,
            branch_id=BRANCH_ID,
            source_revision=4,
        ),
        components,
        credential_kid=CREDENTIAL.kid,
        credential_digest=CREDENTIAL.digest,
        source_generated_at=source_generated_at or NOW - timedelta(days=2),
        snapshot_id=SNAPSHOT_ID,
    )
    manifest = envelope.manifest
    snapshot = SimpleNamespace(
        snapshot_id=manifest.snapshot_id,
        node_id=manifest.edge_node_id,
        tenant_id=manifest.tenant_id,
        branch_id=manifest.branch_id,
        schema_version=manifest.schema_version,
        source_revision=manifest.source_revision,
        source_generated_at=manifest.source_generated_at,
        credential_kid=manifest.credential_kid,
        snapshot_hash=manifest.snapshot_hash,
        manifest_hash=envelope.manifest_hash,
        signature_algorithm=envelope.signature_algorithm,
        signature=envelope.signature,
    )
    component_rows = [
        SimpleNamespace(**component.model_dump()) for component in manifest.components
    ]
    chunks = [
        SimpleNamespace(
            snapshot_id=chunk.snapshot_id,
            node_id=chunk.edge_node_id,
            tenant_id=chunk.tenant_id,
            branch_id=chunk.branch_id,
            source_revision=chunk.source_revision,
            component=chunk.component,
            chunk_index=chunk.chunk_index,
            schema_version=chunk.schema_version,
            item_count=chunk.item_count,
            payload_hash=chunk.payload_hash,
            payload={"items": list(chunk.payload)},
        )
        for chunk in signed_chunks
    ]
    return snapshot, component_rows, chunks, confirmed_at or manifest.source_generated_at


@pytest.fixture(autouse=True)
def _edge_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(
        EDGE_SYNC_CREDENTIAL=SimpleNamespace(get_secret_value=lambda: CREDENTIAL.token),
        EDGE_READ_SNAPSHOT_STALE_SECONDS=86400,
        EDGE_SYNC_MAX_CLOCK_SKEW_SECONDS=300,
    )
    monkeypatch.setattr("app.domains.sync.read_model.get_settings", lambda: settings)


@pytest.mark.asyncio
async def test_catalog_barcode_and_inventory_use_local_snapshot_with_freshness() -> None:
    service = LocalReadModel(cast(SyncEdgeRepository, _FakeRepo(_stored_snapshot())))

    catalog = await service.catalog(
        tenant_id=TENANT_ID,
        branch_id=BRANCH_ID,
        query="пара",
        page=1,
        page_size=20,
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
async def test_missing_active_snapshot_is_explicitly_unavailable() -> None:
    service = LocalReadModel(cast(SyncEdgeRepository, _FakeRepo(None)))

    with pytest.raises(ServiceUnavailableError, match="not installed"):
        await service.foundation(tenant_id=TENANT_ID, branch_id=BRANCH_ID)


@pytest.mark.asyncio
async def test_tampered_active_snapshot_is_never_served() -> None:
    snapshot, components, chunks, confirmed_at = _stored_snapshot()
    chunks[1].payload["items"][0]["brand_name"] = "Tampered"
    service = LocalReadModel(
        cast(
            SyncEdgeRepository,
            _FakeRepo((snapshot, components, chunks, confirmed_at)),
        )
    )

    with pytest.raises(ServiceUnavailableError, match="corrupted"):
        await service.catalog(
            tenant_id=TENANT_ID,
            branch_id=BRANCH_ID,
            query=None,
            page=1,
            page_size=20,
        )


@pytest.mark.asyncio
async def test_signed_cross_tenant_payload_is_never_served() -> None:
    wrong_tenant = UUID("20000000-0000-0000-0000-000000000077")
    service = LocalReadModel(
        cast(
            SyncEdgeRepository,
            _FakeRepo(_stored_snapshot(catalog_tenant_id=wrong_tenant)),
        )
    )

    with pytest.raises(ServiceUnavailableError, match="corrupted"):
        await service.catalog(
            tenant_id=TENANT_ID,
            branch_id=BRANCH_ID,
            query=None,
            page=1,
            page_size=20,
        )


@pytest.mark.asyncio
async def test_snapshot_from_far_future_is_never_reported_as_fresh() -> None:
    service = LocalReadModel(
        cast(
            SyncEdgeRepository,
            _FakeRepo(
                _stored_snapshot(
                    source_generated_at=datetime.now(UTC) + timedelta(hours=1),
                    confirmed_at=datetime.now(UTC),
                )
            ),
        )
    )

    with pytest.raises(ServiceUnavailableError, match="clock is invalid"):
        await service.foundation(tenant_id=TENANT_ID, branch_id=BRANCH_ID)


@pytest.mark.asyncio
async def test_recent_cloud_confirmation_keeps_unchanged_snapshot_fresh() -> None:
    service = LocalReadModel(
        cast(
            SyncEdgeRepository,
            _FakeRepo(_stored_snapshot(confirmed_at=datetime.now(UTC))),
        )
    )

    result = await service.foundation(tenant_id=TENANT_ID, branch_id=BRANCH_ID)

    assert result.metadata.source_generated_at < datetime.now(UTC) - timedelta(days=1)
    assert result.metadata.last_confirmed_at > datetime.now(UTC) - timedelta(minutes=1)
    assert result.metadata.is_stale is False
