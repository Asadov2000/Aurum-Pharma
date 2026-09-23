"""Cloud transport and atomic Edge storage for read-only snapshots."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
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
from app.domains.sync.credentials import EdgeCredential
from app.domains.sync.read_model import LocalReadModel
from app.domains.sync.read_snapshot import ReadSnapshotScope, build_manifest, verify_chunk
from app.domains.sync.read_snapshot_projection import (
    BarcodeSnapshot,
    IndexedReadProjection,
    build_component_chunks,
)
from app.domains.sync.repository import SyncCloudRepository, SyncEdgeRepository
from app.domains.sync.service import SyncCloudService
from tests.domains.sync.test_local_read_model import _batch, _foundation, _product


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


def _projection(*, tenant_id: UUID, branch_id: UUID) -> IndexedReadProjection:
    base = _foundation()
    foundation = base.model_copy(
        update={
            "tenant": base.tenant.model_copy(update={"id": tenant_id}),
            "branch": base.branch.model_copy(update={"id": branch_id, "tenant_id": tenant_id}),
        }
    )
    product = _product(tenant_id=tenant_id).model_copy(
        update={"brand_name": "Парацетамол 1234", "manufacturer": "Straße"}
    )
    literal_product = product.model_copy(
        update={
            "id": UUID("20000000-0000-0000-0000-000000000012"),
            "brand_name": "Literal 100%_\\safe",
            "inn": "literal",
            "manufacturer": "Aurum",
            "barcodes": (
                BarcodeSnapshot(
                    id=UUID("20000000-0000-0000-0000-000000000013"),
                    code="87654321",
                    code_type="ean8",
                ),
            ),
        }
    )
    wildcard_product = product.model_copy(
        update={
            "id": UUID("20000000-0000-0000-0000-000000000014"),
            "brand_name": "Literal 100ABCsafe",
            "inn": "wildcard",
            "manufacturer": "Aurum",
            "barcodes": (
                BarcodeSnapshot(
                    id=UUID("20000000-0000-0000-0000-000000000015"),
                    code="11223344",
                    code_type="ean8",
                ),
            ),
        }
    )
    batch = _batch().model_copy(
        update={"tenant_id": tenant_id, "branch_id": branch_id, "catalog_id": product.id}
    )
    empty_batch = batch.model_copy(
        update={
            "id": UUID("20000000-0000-0000-0000-000000000021"),
            "batch_number": "LOT-EMPTY",
            "expires_at": date.today() - timedelta(days=1),
            "qty_remaining": Decimal("0"),
            "is_blocked": True,
            "block_reason": "expired",
        }
    )
    return IndexedReadProjection(
        foundation=foundation,
        catalog=(product, literal_product, wildcard_product),
        suppliers=(),
        inventory=(empty_batch, batch),
    )


async def _assert_verified_local_read_model(
    *,
    repo: SyncEdgeRepository,
    tenant_id: UUID,
    branch_id: UUID,
    snapshot_id: UUID,
    edge_credential: EdgeCredential,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        EDGE_SYNC_CREDENTIAL=SimpleNamespace(get_secret_value=lambda: edge_credential.token),
        EDGE_READ_SNAPSHOT_STALE_SECONDS=86400,
        EDGE_SYNC_MAX_CLOCK_SKEW_SECONDS=300,
    )
    monkeypatch.setattr("app.domains.sync.read_model.get_settings", lambda: settings)
    read_model = LocalReadModel(repo)
    foundation = await read_model.foundation(tenant_id=tenant_id, branch_id=branch_id)
    catalog = await read_model.catalog(
        tenant_id=tenant_id,
        branch_id=branch_id,
        query="пара",
        page=1,
        page_size=20,
    )
    assert foundation.metadata.snapshot_id == snapshot_id
    assert catalog.total == 1


async def _assert_catalog_projection_search(
    *,
    repo: SyncEdgeRepository,
    snapshot_id: UUID,
    product_id: UUID,
    literal_product_id: UUID,
) -> None:
    catalog_rows, catalog_total = await repo.list_read_catalog_projection(
        snapshot_id=snapshot_id,
        query="пара",
        offset=0,
        limit=20,
    )
    assert catalog_total == 1
    assert catalog_rows[0].catalog_id == product_id

    barcode_rows, barcode_total = await repo.list_read_catalog_projection(
        snapshot_id=snapshot_id,
        query="3456",
        offset=0,
        limit=20,
    )
    assert barcode_total == 1
    assert barcode_rows[0].catalog_id == product_id

    duplicate_rows, duplicate_total = await repo.list_read_catalog_projection(
        snapshot_id=snapshot_id,
        query="1234",
        offset=0,
        limit=20,
    )
    assert duplicate_total == 1
    assert duplicate_rows[0].catalog_id == product_id

    literal_rows, literal_total = await repo.list_read_catalog_projection(
        snapshot_id=snapshot_id,
        query="100%_\\safe",
        offset=0,
        limit=20,
    )
    assert literal_total == 1
    assert literal_rows[0].catalog_id == literal_product_id

    page_rows, page_total = await repo.list_read_catalog_projection(
        snapshot_id=snapshot_id,
        query=None,
        offset=1,
        limit=1,
    )
    assert page_total == 3
    assert len(page_rows) == 1


async def _assert_catalog_search_rejects_cross_scope(
    *,
    connection: AsyncConnection,
    snapshot_id: UUID,
    tenant_id: UUID,
    branch_id: UUID,
    node_id: UUID,
) -> None:
    foreign_id = uuid4()
    for scoped_tenant, scoped_branch, scoped_node in (
        (foreign_id, branch_id, node_id),
        (tenant_id, foreign_id, node_id),
        (tenant_id, branch_id, foreign_id),
    ):
        await _set_edge_scope(
            connection,
            tenant_id=scoped_tenant,
            branch_id=scoped_branch,
            node_id=scoped_node,
        )
        leaked = await connection.scalar(
            text("SELECT count(*) FROM public.search_sync_read_catalog(" ":snapshot_id, 'пара')"),
            {"snapshot_id": snapshot_id},
        )
        assert leaked == 0
    await _set_edge_scope(
        connection,
        tenant_id=tenant_id,
        branch_id=branch_id,
        node_id=node_id,
    )


async def _assert_catalog_projection_access(
    *,
    repo: SyncEdgeRepository,
    connection: AsyncConnection,
    snapshot_id: UUID,
    product_id: UUID,
    literal_product_id: UUID,
    tenant_id: UUID,
    branch_id: UUID,
    node_id: UUID,
) -> None:
    await _set_edge_scope(
        connection,
        tenant_id=tenant_id,
        branch_id=branch_id,
        node_id=node_id,
    )
    await _assert_catalog_projection_search(
        repo=repo,
        snapshot_id=snapshot_id,
        product_id=product_id,
        literal_product_id=literal_product_id,
    )
    await _assert_catalog_search_rejects_cross_scope(
        connection=connection,
        snapshot_id=snapshot_id,
        tenant_id=tenant_id,
        branch_id=branch_id,
        node_id=node_id,
    )


async def _assert_inventory_rollup(
    *,
    repo: SyncEdgeRepository,
    connection: AsyncConnection,
    snapshot_id: UUID,
    expected_batch_ids: tuple[UUID, ...],
) -> None:
    await _assert_inventory_physical_order(
        connection, snapshot_id=snapshot_id, expected_ids=expected_batch_ids
    )
    available, available_total, available_summary = await repo.list_read_inventory_projection(
        snapshot_id=snapshot_id,
        show_empty=False,
        offset=0,
        limit=20,
        today=date.today(),
        orange_until=date.today() + timedelta(days=90),
    )
    all_batches, all_total, all_summary = await repo.list_read_inventory_projection(
        snapshot_id=snapshot_id,
        show_empty=True,
        offset=0,
        limit=20,
        today=date.today(),
        orange_until=date.today() + timedelta(days=90),
    )
    assert len(available) == available_total == 1
    assert available_summary.total_qty == Decimal("8")
    assert available_summary.attention_count == 0
    assert len(all_batches) == all_total == 2
    assert all_summary.total_qty == Decimal("8")
    assert all_summary.purchase_value == Decimal("24")
    assert all_summary.sale_value == Decimal("40")
    assert all_summary.attention_count == 1
    assert all_summary.expired_count == 1
    assert all_summary.blocked_count == 1
    second_page, second_total, second_summary = await repo.list_read_inventory_projection(
        snapshot_id=snapshot_id,
        show_empty=True,
        offset=1,
        limit=1,
        today=date.today(),
        orange_until=date.today() + timedelta(days=90),
    )
    empty_page, empty_total, empty_summary = await repo.list_read_inventory_projection(
        snapshot_id=snapshot_id,
        show_empty=True,
        offset=2,
        limit=1,
        today=date.today(),
        orange_until=date.today() + timedelta(days=90),
    )
    assert second_page == all_batches[1:]
    assert second_total == all_total
    assert second_summary == all_summary
    assert empty_page == []
    assert empty_total == all_total
    assert empty_summary == all_summary
    audit_values = (
        (
            await connection.execute(
                text(
                    "SELECT new_values FROM audit_log "
                    "WHERE table_name = 'sync_read_inventory_rollup' "
                    "AND record_id = :snapshot_id"
                ),
                {"snapshot_id": snapshot_id},
            )
        )
        .scalars()
        .all()
    )
    assert audit_values
    assert all(
        "total_qty" not in values and "purchase_value" not in values and "sale_value" not in values
        for values in audit_values
    )


async def _assert_inventory_physical_order(
    connection: AsyncConnection, *, snapshot_id: UUID, expected_ids: tuple[UUID, ...]
) -> None:
    physical_batch_ids = (
        (
            await connection.execute(
                text(
                    "SELECT batch_id FROM sync_read_inventory_projection "
                    "WHERE snapshot_id=:snapshot_id ORDER BY ctid"
                ),
                {"snapshot_id": snapshot_id},
            )
        )
        .scalars()
        .all()
    )
    assert physical_batch_ids == sorted(expected_ids)


async def test_cloud_serves_only_the_nodes_complete_signed_snapshot(
    support_engine_read_transport: AsyncEngine,
    app_engine_read_transport: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_kid = uuid4()
    edge_credential = EdgeCredential(kid=credential_kid, secret=uuid4().hex * 2)
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

    projection = _projection(tenant_id=tenant_id, branch_id=branch_id)
    product = projection.catalog[0]
    literal_product = projection.catalog[1]
    component_chunks = build_component_chunks(
        foundation=projection.foundation,
        catalog=projection.catalog,
        suppliers=projection.suppliers,
        inventory=projection.inventory,
        chunk_size=100,
    )
    envelope, chunks = build_manifest(
        ReadSnapshotScope(
            edge_node_id=edge_node_id,
            tenant_id=tenant_id,
            branch_id=branch_id,
            source_revision=1,
        ),
        component_chunks,
        credential_kid=credential_kid,
        credential_digest=edge_credential.digest,
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
            forged = IndexedReadProjection(
                foundation=projection.foundation,
                catalog=(product.model_copy(update={"base_price": Decimal("6.00")}),),
                suppliers=projection.suppliers,
                inventory=projection.inventory,
            )
            with pytest.raises(DBAPIError, match="does not match immutable snapshot content"):
                async with session.begin_nested():
                    await SyncEdgeRepository(session).install_read_snapshot(
                        envelope=envelope,
                        chunks=chunks,
                        projection=forged,
                    )
            await SyncEdgeRepository(session).install_read_snapshot(
                envelope=envelope,
                chunks=chunks,
                projection=projection,
            )

    async with app_engine_read_transport.begin() as connection:
        await _set_edge_scope(
            connection,
            tenant_id=tenant_id,
            branch_id=branch_id,
            node_id=edge_node_id,
        )
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            repo = SyncEdgeRepository(session)
            before_confirmation = (
                await connection.execute(
                    text(
                        "SELECT version, activated_at, last_confirmed_at "
                        "FROM sync_read_snapshot_cursor "
                        "WHERE tenant_id=:tenant_id AND branch_id=:branch_id AND node_id=:node_id"
                    ),
                    {"tenant_id": tenant_id, "branch_id": branch_id, "node_id": edge_node_id},
                )
            ).one()
            await repo.confirm_active_read_snapshot(
                tenant_id=tenant_id,
                branch_id=branch_id,
                edge_node_id=edge_node_id,
                snapshot_id=envelope.manifest.snapshot_id,
            )
            await connection.execute(text("SELECT set_config('app.branch_id', '', true)"))
            await connection.execute(text("SELECT set_config('app.edge_node_id', '', true)"))
            active_projection = await repo.get_active_read_projection(
                tenant_id=tenant_id,
                branch_id=branch_id,
                credential_kid=credential_kid,
            )
            assert active_projection is not None
            await _assert_catalog_projection_access(
                repo=repo,
                connection=connection,
                snapshot_id=envelope.manifest.snapshot_id,
                product_id=product.id,
                literal_product_id=literal_product.id,
                tenant_id=tenant_id,
                branch_id=branch_id,
                node_id=edge_node_id,
            )
            await _assert_inventory_rollup(
                repo=repo,
                connection=connection,
                snapshot_id=envelope.manifest.snapshot_id,
                expected_batch_ids=tuple(row.id for row in projection.inventory),
            )
            await _assert_verified_local_read_model(
                repo=repo,
                tenant_id=tenant_id,
                branch_id=branch_id,
                snapshot_id=envelope.manifest.snapshot_id,
                edge_credential=edge_credential,
                monkeypatch=monkeypatch,
            )
        cursor = (
            await connection.execute(
                text(
                    "SELECT version, active_snapshot_id, activated_at, last_confirmed_at "
                    "FROM sync_read_snapshot_cursor "
                    "WHERE tenant_id=:tenant_id AND branch_id=:branch_id AND node_id=:node_id"
                ),
                {"tenant_id": tenant_id, "branch_id": branch_id, "node_id": edge_node_id},
            )
        ).one()
        assert cursor.version == before_confirmation.version == 1
        assert cursor.active_snapshot_id == envelope.manifest.snapshot_id
        assert cursor.activated_at == before_confirmation.activated_at
        assert cursor.last_confirmed_at >= before_confirmation.last_confirmed_at

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
