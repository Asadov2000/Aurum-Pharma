"""Cloud publisher creates complete branch-scoped read snapshots."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domains.sync.credentials import EdgeCredential
from app.domains.sync.repository import SyncCloudRepository
from app.domains.sync.service import SyncCloudService


@pytest_asyncio.fixture
async def support_engine_snapshot_publisher() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_engine_snapshot_publisher() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_publisher_is_complete_scoped_and_monotonic(
    support_engine_snapshot_publisher: AsyncEngine,
    app_engine_snapshot_publisher: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.domains.sync.service.get_settings",
        lambda: SimpleNamespace(
            EDGE_READ_SNAPSHOT_CHUNK_SIZE=500,
            EDGE_READ_SNAPSHOT_REFRESH_SECONDS=0,
        ),
    )
    credential = EdgeCredential(kid=uuid4(), secret=uuid4().hex * 2)
    async with support_engine_snapshot_publisher.begin() as connection:
        tenant_id = (
            await connection.execute(
                text(
                    "INSERT INTO tenant (name, contact_email) "
                    "VALUES (:name, :email) RETURNING id"
                ),
                {
                    "name": f"Publisher {uuid4().hex[:8]}",
                    "email": f"publisher-{uuid4().hex[:8]}@aurum.tj",
                },
            )
        ).scalar_one()
        await connection.execute(
            text("""
                INSERT INTO tenant_settings (tenant_id, prescription_warning_text)
                VALUES (:tenant_id, 'Prescription required')
                """),
            {"tenant_id": tenant_id},
        )
        branch_id = (
            await connection.execute(
                text(
                    "INSERT INTO branch (tenant_id, name) "
                    "VALUES (:tenant_id, 'Publisher branch') RETURNING id"
                ),
                {"tenant_id": tenant_id},
            )
        ).scalar_one()
        other_branch_id = (
            await connection.execute(
                text(
                    "INSERT INTO branch (tenant_id, name) "
                    "VALUES (:tenant_id, 'Other branch') RETURNING id"
                ),
                {"tenant_id": tenant_id},
            )
        ).scalar_one()
        cloud_node_id = (
            await connection.execute(
                text(
                    "SELECT writer_node_id FROM sync_stream "
                    "WHERE tenant_id=:tenant_id AND branch_id=:branch_id"
                ),
                {"tenant_id": tenant_id, "branch_id": branch_id},
            )
        ).scalar_one()
        edge_node_id = (
            await connection.execute(
                text("""
                    INSERT INTO sync_node (
                      tenant_id, branch_id, node_kind, mode, status, display_name,
                      credential_kid, credential_hash, credential_issued_at,
                      credential_expires_at, shadow_start_origin_node_id,
                      shadow_start_writer_epoch
                    ) VALUES (
                      :tenant_id, :branch_id, 'edge', 'shadow_readonly', 'active',
                      'Publisher Edge', :credential_kid, :credential_hash, now(),
                      now() + interval '1 day', :cloud_node_id, 1
                    ) RETURNING id
                    """),
                {
                    "tenant_id": tenant_id,
                    "branch_id": branch_id,
                    "credential_kid": credential.kid,
                    "credential_hash": credential.digest,
                    "cloud_node_id": cloud_node_id,
                },
            )
        ).scalar_one()
        catalog_id = (
            await connection.execute(
                text("""
                    INSERT INTO tenant_catalog (tenant_id, brand_name, base_price)
                    VALUES (:tenant_id, 'Парацетамол', 12.50) RETURNING id
                    """),
                {"tenant_id": tenant_id},
            )
        ).scalar_one()
        await connection.execute(
            text("""
                INSERT INTO barcode (tenant_id, catalog_id, code, code_type)
                VALUES (:tenant_id, :catalog_id, '12345678', 'ean8')
                """),
            {"tenant_id": tenant_id, "catalog_id": catalog_id},
        )
        await connection.execute(
            text("""
                INSERT INTO batch (
                  tenant_id, branch_id, catalog_id, expires_at,
                  purchase_price, sale_price, qty_initial, qty_remaining
                ) VALUES
                  (:tenant_id, :branch_id, :catalog_id, CURRENT_DATE + 365, 10, 12.5, 5, 5),
                  (:tenant_id, :other_branch_id, :catalog_id, CURRENT_DATE + 365, 10, 12.5, 9, 9)
                """),
            {
                "tenant_id": tenant_id,
                "branch_id": branch_id,
                "other_branch_id": other_branch_id,
                "catalog_id": catalog_id,
            },
        )

    async with app_engine_snapshot_publisher.begin() as connection:
        for setting, value in (
            ("app.tenant_id", tenant_id),
            ("app.branch_id", branch_id),
            ("app.edge_node_id", edge_node_id),
        ):
            await connection.execute(
                text("SELECT set_config(:setting, :value, true)"),
                {"setting": setting, "value": str(value)},
            )
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            service = SyncCloudService(SyncCloudRepository(session))
            first = await service.publish_read_snapshot(
                edge_node_id=edge_node_id,
                expected_tenant_id=tenant_id,
                expected_branch_id=branch_id,
                expected_credential_kid=credential.kid,
                credential_digest=credential.digest,
            )
            second = await service.publish_read_snapshot(
                edge_node_id=edge_node_id,
                expected_tenant_id=tenant_id,
                expected_branch_id=branch_id,
                expected_credential_kid=credential.kid,
                credential_digest=credential.digest,
            )
            await connection.execute(
                text(
                    "UPDATE tenant_catalog SET base_price = 13.00, updated_at = now() "
                    "WHERE tenant_id = :tenant_id AND id = :catalog_id"
                ),
                {"tenant_id": tenant_id, "catalog_id": catalog_id},
            )
            third = await service.publish_read_snapshot(
                edge_node_id=edge_node_id,
                expected_tenant_id=tenant_id,
                expected_branch_id=branch_id,
                expected_credential_kid=credential.kid,
                credential_digest=credential.digest,
            )
            chunks = await service.repo.list_read_snapshot_chunks(
                snapshot_id=third.manifest.snapshot_id,
                tenant_id=tenant_id,
                branch_id=branch_id,
                edge_node_id=edge_node_id,
            )

    assert first.manifest.source_revision == 1
    assert second.manifest.snapshot_id == first.manifest.snapshot_id
    assert third.manifest.source_revision == 2
    assert [component.component for component in third.manifest.components] == [
        "foundation",
        "catalog",
        "inventory",
    ]
    inventory_items = [
        item
        for chunk in chunks
        if chunk.component == "inventory"
        for item in chunk.payload["items"]
    ]
    assert len(inventory_items) == 1
    assert inventory_items[0]["branch_id"] == str(branch_id)
