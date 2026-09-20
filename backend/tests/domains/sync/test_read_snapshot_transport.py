"""Cloud transport and atomic Edge storage for read-only snapshots."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.time import utc_now
from app.domains.sync.read_snapshot import ReadSnapshotScope, build_manifest, verify_chunk
from app.domains.sync.repository import SyncCloudRepository, SyncEdgeRepository
from app.domains.sync.service import SyncCloudService


@pytest_asyncio.fixture
async def support_engine_read_transport() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_engine_read_transport() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _set_edge_scope(
    connection: AsyncConnection,
    *,
    tenant_id: UUID,
    branch_id: UUID,
    node_id: UUID,
) -> None:
    for setting, value in (
        ("app.tenant_id", tenant_id),
        ("app.branch_id", branch_id),
        ("app.edge_node_id", node_id),
    ):
        await connection.execute(
            text("SELECT set_config(:setting, :value, true)"),
            {"setting": setting, "value": str(value)},
        )


async def test_cloud_serves_only_the_nodes_complete_signed_snapshot(
    support_engine_read_transport: AsyncEngine,
    app_engine_read_transport: AsyncEngine,
) -> None:
    credential_kid = uuid4()
    credential_digest = uuid4().hex * 2
    async with support_engine_read_transport.begin() as connection:
        tenant_id = (
            await connection.execute(
                text(
                    "INSERT INTO tenant (name, contact_email) "
                    "VALUES (:name, :email) RETURNING id"
                ),
                {
                    "name": f"Read transport {uuid4().hex[:8]}",
                    "email": f"read-transport-{uuid4().hex[:8]}@aurum.tj",
                },
            )
        ).scalar_one()
        branch_id = (
            await connection.execute(
                text(
                    "INSERT INTO branch (tenant_id, name) "
                    "VALUES (:tenant_id, 'Read transport') RETURNING id"
                ),
                {"tenant_id": tenant_id},
            )
        ).scalar_one()
        cloud_node_id = (
            await connection.execute(
                text(
                    "SELECT writer_node_id FROM sync_stream "
                    "WHERE tenant_id = :tenant_id AND branch_id = :branch_id"
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
                      'Read transport Edge', :credential_kid, :credential_hash, now(),
                      now() + interval '1 day', :cloud_node_id, 1
                    ) RETURNING id
                    """),
                {
                    "tenant_id": tenant_id,
                    "branch_id": branch_id,
                    "credential_kid": credential_kid,
                    "credential_hash": uuid4().hex * 2,
                    "cloud_node_id": cloud_node_id,
                },
            )
        ).scalar_one()

    envelope, chunks = build_manifest(
        ReadSnapshotScope(
            edge_node_id=edge_node_id,
            tenant_id=tenant_id,
            branch_id=branch_id,
            source_revision=1,
        ),
        {
            "foundation": [[{"currency": "TJS"}]],
            "catalog": [[{"id": "product-1", "name": "Парацетамол"}]],
            "inventory": [],
        },
        credential_kid=credential_kid,
        credential_digest=credential_digest,
        source_generated_at=utc_now(),
    )
    async with app_engine_read_transport.begin() as connection:
        await _set_edge_scope(
            connection,
            tenant_id=tenant_id,
            branch_id=branch_id,
            node_id=edge_node_id,
        )
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            await SyncEdgeRepository(session).install_read_snapshot(
                envelope=envelope,
                chunks=chunks,
            )

    async with app_engine_read_transport.begin() as connection:
        await _set_edge_scope(
            connection,
            tenant_id=tenant_id,
            branch_id=branch_id,
            node_id=edge_node_id,
        )
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            service = SyncCloudService(SyncCloudRepository(session))
            served = await service.read_snapshot_manifest(
                edge_node_id=edge_node_id,
                tenant_id=tenant_id,
                branch_id=branch_id,
                credential_kid=credential_kid,
            )
            assert served == envelope
            expected_catalog = next(chunk for chunk in chunks if chunk.component == "catalog")
            served_catalog = await service.read_snapshot_chunk(
                snapshot_id=envelope.manifest.snapshot_id,
                component="catalog",
                chunk_index=0,
                edge_node_id=edge_node_id,
                tenant_id=tenant_id,
                branch_id=branch_id,
                credential_kid=credential_kid,
            )
            assert served_catalog == expected_catalog
            verify_chunk(envelope.manifest, served_catalog)

            with pytest.raises(NotFoundError, match="Read snapshot not found"):
                await service.read_snapshot_manifest(
                    edge_node_id=uuid4(),
                    tenant_id=tenant_id,
                    branch_id=branch_id,
                    credential_kid=credential_kid,
                )
