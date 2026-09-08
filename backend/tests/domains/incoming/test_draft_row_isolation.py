"""Exercise draft RLS through real roles and two committed tenant fixtures."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domains.incoming.draft_service import IncomingDraftService
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.schemas import IncomingDraftFields
from app.domains.incoming.service import IncomingService
from tests.domains.incoming import test_acceptance_concurrency as acceptance_fixtures
from tests.domains.incoming.test_acceptance_concurrency import IncomingFixture

committed_incoming = acceptance_fixtures.committed_incoming
second_committed_incoming = acceptance_fixtures.committed_incoming

_FIELDS = IncomingDraftFields(
    catalog_id="",
    batch_number="",
    manufactured_at="",
    expires_at="",
    qty="12,",
    purchase_price="44,23",
    sale_price="",
)


async def _set_tenant(connection: AsyncConnection, tenant_id: UUID | None) -> None:
    await connection.execute(
        text("SELECT set_config('app.tenant_id', :tenant, true)"),
        {"tenant": str(tenant_id) if tenant_id is not None else ""},
    )


async def _assert_scope(
    engine: AsyncEngine,
    *,
    tenant_id: UUID | None,
    visible: list[UUID],
    hidden: list[UUID],
) -> None:
    async with engine.begin() as connection:
        assert await connection.scalar(text("SELECT current_user")) == "aurum_app"
        await _set_tenant(connection, tenant_id)
        result = await connection.execute(
            text("SELECT id FROM incoming_draft_row WHERE id = ANY(:ids)"),
            {"ids": visible + hidden},
        )
        assert set(result.scalars()) == set(visible)
        returned = await connection.execute(
            text(
                "UPDATE incoming_draft_row SET revision=revision+1 "
                "WHERE id = ANY(:ids) RETURNING id"
            ),
            {"ids": hidden},
        )
        assert returned.all() == []
        if visible:
            changed = await connection.execute(
                text(
                    "UPDATE incoming_draft_row SET revision=revision+1 "
                    "WHERE id = ANY(:ids) RETURNING id"
                ),
                {"ids": visible},
            )
            assert set(changed.scalars()) == set(visible)


async def _assert_insert_rejected(
    engine: AsyncEngine,
    *,
    current_tenant: UUID | None,
    tenant_id: UUID,
    document_id: UUID,
) -> None:
    with pytest.raises(DBAPIError) as rejected:
        async with engine.begin() as connection:
            await _set_tenant(connection, current_tenant)
            await connection.execute(
                text(
                    "INSERT INTO incoming_draft_row (tenant_id,id,document_id,revision,fields) "
                    "VALUES (:tenant,:id,:document,1,CAST(:fields AS jsonb))"
                ),
                {
                    "tenant": tenant_id,
                    "id": uuid4(),
                    "document": document_id,
                    "fields": json.dumps(_FIELDS.as_fields()),
                },
            )
    # The document guard can reject before the FK or RLS WITH CHECK executes.
    assert rejected.value.orig.sqlstate in {"23503", "23514", "42501"}


async def _assert_delete_denied(engine: AsyncEngine, tenant_id: UUID, row_id: UUID) -> None:
    with pytest.raises(DBAPIError) as rejected:
        async with engine.begin() as connection:
            await _set_tenant(connection, tenant_id)
            await connection.execute(
                text("DELETE FROM incoming_draft_row WHERE id=:id"),
                {"id": row_id},
            )
    assert rejected.value.orig.sqlstate == "42501"


async def test_committed_draft_is_tenant_isolated_and_force_rls(
    committed_incoming: IncomingFixture,
    second_committed_incoming: IncomingFixture,
    maintenance_engine: AsyncEngine,
) -> None:
    fixtures = (committed_incoming, second_committed_incoming)
    first, second = (fixture[1] for fixture in fixtures)
    assert first.tenant_id != second.tenant_id
    if not (maintenance_engine.url.database or "").endswith("_test"):
        raise RuntimeError("Draft isolation requires a disposable database")
    rows = (uuid4(), uuid4())
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    support = create_async_engine(get_settings().DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        for (factory, context), row_id in zip(fixtures, rows, strict=True):
            async with factory.begin() as session:
                await IncomingDraftService(IncomingService(IncomingRepository(session))).save(
                    context.document_ids[0],
                    row_id,
                    expected_revision=0,
                    actor_id=uuid4(),
                    fields=_FIELDS,
                )
        await _assert_scope(engine, tenant_id=first.tenant_id, visible=[rows[0]], hidden=[rows[1]])
        await _assert_scope(engine, tenant_id=second.tenant_id, visible=[rows[1]], hidden=[rows[0]])
        await _assert_scope(engine, tenant_id=None, visible=[], hidden=list(rows))
        # Own tenant plus the other tenant's real document must fail too.
        await _assert_insert_rejected(
            engine,
            current_tenant=first.tenant_id,
            tenant_id=first.tenant_id,
            document_id=second.document_ids[0],
        )
        await _assert_insert_rejected(
            engine,
            current_tenant=first.tenant_id,
            tenant_id=second.tenant_id,
            document_id=second.document_ids[0],
        )
        await _assert_insert_rejected(
            engine,
            current_tenant=None,
            tenant_id=first.tenant_id,
            document_id=first.document_ids[0],
        )
        await _assert_delete_denied(engine, first.tenant_id, rows[0])
        async with support.begin() as connection:
            assert await connection.scalar(text("SELECT current_user")) == "aurum_support"
            await _set_tenant(connection, None)
            assert (
                await connection.scalar(
                    text(
                        "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                        "WHERE oid='incoming_draft_row'::regclass"
                    )
                )
                is True
            )
            result = await connection.execute(
                text(
                    "SELECT id,revision,fields->>'purchase_price' FROM incoming_draft_row "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": list(rows)},
            )
            # The genuine BYPASSRLS support login can observe both tenants.
            assert set(result.all()) == {(row_id, 2, "44,23") for row_id in rows}
    finally:
        await engine.dispose()
        await support.dispose()
        # Owner DDL is restricted to this disposable database. Restore the guard
        # atomically before either committed tenant fixture tears down its data.
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("ALTER TABLE incoming_draft_row DISABLE TRIGGER trg_incoming_draft_guard")
            )
            for (_, context), row_id in zip(fixtures, rows, strict=True):
                await connection.execute(
                    text("DELETE FROM incoming_draft_row WHERE tenant_id=:tenant AND id=:id"),
                    {"tenant": context.tenant_id, "id": row_id},
                )
            await connection.execute(
                text("ALTER TABLE incoming_draft_row ENABLE TRIGGER trg_incoming_draft_guard")
            )
