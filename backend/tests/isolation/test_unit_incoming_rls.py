"""Prepared receiving enforces live PostgreSQL actor, branch and tenant scope."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import NotFoundError, PermissionDeniedError
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.service import IncomingService
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    accept_prepared,
    assert_draft_without_stock,
    seed_prepared_incoming,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import (
    db_session as receiving_session_fixture,
)
from tests.domains.inventory.test_unit_ledger import LedgerContext, seed_ledger_context

db_session = receiving_session_fixture


async def test_cashier_without_receiving_permission_cannot_accept_or_replay(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    incoming = await seed_prepared_incoming(db_session)
    await db_session.commit()
    cashier = await seed_receiving_actor(
        db_engine, maintenance_engine, incoming.stock, permissions=("incoming.view", "pos.sell")
    )
    await set_receiving_context(db_session, incoming.stock, cashier)
    with pytest.raises(PermissionDeniedError):
        await IncomingService(IncomingRepository(db_session)).accept_prepared(
            incoming.document_id, actor_id=cashier, allowed_branch_ids={incoming.stock.branch_id}
        )
    await set_receiving_context(db_session, incoming.stock, runtime.actor_id)
    await assert_draft_without_stock(db_session, incoming)
    await accept_prepared(db_session, incoming)
    await set_receiving_context(db_session, incoming.stock, cashier)
    with pytest.raises(PermissionDeniedError):
        await IncomingService(IncomingRepository(db_session)).accept_prepared(
            incoming.document_id, actor_id=cashier, allowed_branch_ids={incoming.stock.branch_id}
        )


async def test_foreign_branch_and_tenant_cannot_be_received_with_forged_allowed_scope(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            other_branch = await support.scalar(
                text(
                    "INSERT INTO branch (tenant_id,name) "
                    "VALUES (:tenant,'Foreign branch') RETURNING id"
                ),
                {"tenant": runtime.stock.tenant_id},
            )
            assert isinstance(other_branch, UUID)
            other_tenant = await seed_ledger_context(support)
    foreign_branch = LedgerContext(runtime.stock.tenant_id, other_branch, runtime.stock.catalog_id)
    for foreign in (foreign_branch, other_tenant):
        actor = await seed_receiving_actor(db_engine, maintenance_engine, foreign)
        await set_receiving_context(db_session, foreign, actor)
        db_session.info["receiving_runtime"] = ReceivingRuntime(runtime.factory, foreign, actor)
        document = await seed_prepared_incoming(db_session)
        await db_session.commit()
        db_session.info["receiving_runtime"] = runtime
        await set_receiving_context(db_session, runtime.stock, runtime.actor_id)
        # Legacy incoming tables use tenant RLS; branch access is checked by
        # the service and live scoped permission. New unit stock has branch RLS.
        if foreign.tenant_id != runtime.stock.tenant_id:
            assert (
                await db_session.scalar(
                    text("SELECT count(*) FROM incoming_document WHERE id=:id"),
                    {"id": document.document_id},
                )
                == 0
            )
            assert (
                await db_session.scalar(
                    text(
                        "SELECT unit_packaging_snapshot_v2 FROM incoming_item WHERE document_id=:id"
                    ),
                    {"id": document.document_id},
                )
                is None
            )
            assert (
                await db_session.scalar(
                    text("SELECT count(*) FROM incoming_item WHERE document_id=:id"),
                    {"id": document.document_id},
                )
                == 0
            )
        expected_error = (
            PermissionDeniedError if foreign.tenant_id == runtime.stock.tenant_id else NotFoundError
        )
        with pytest.raises(expected_error):
            await IncomingService(IncomingRepository(db_session)).accept_prepared(
                document.document_id,
                actor_id=runtime.actor_id,
                allowed_branch_ids={foreign.branch_id},
            )
        await set_receiving_context(db_session, foreign, actor)
        assert (
            await db_session.scalar(
                text("SELECT status FROM incoming_document WHERE id=:id"),
                {"id": document.document_id},
            )
            == "draft"
        )
        assert (
            await db_session.scalar(
                text("SELECT count(*) FROM unit_stock_lot WHERE branch_id=:branch"),
                {"branch": foreign.branch_id},
            )
            == 0
        )


async def test_app_cannot_mutate_accepted_batch_blister_history(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_prepared_incoming(db_session)
    await IncomingService(IncomingRepository(db_session)).accept(
        incoming.document_id, allowed_branch_ids={incoming.stock.branch_id}
    )
    batch_id = await db_session.scalar(
        text("SELECT created_batch_id FROM incoming_item WHERE id=:id"),
        {"id": incoming.item_ids[0]},
    )
    assert batch_id is not None
    assert await db_session.scalar(text("SELECT current_user")) == "aurum_app"
    for value in ("NULL", "jsonb_set(unit_packaging_snapshot_v2,'{units_per_blister}','10')"):
        with pytest.raises(DBAPIError):
            async with db_session.begin_nested():
                await db_session.execute(
                    text(f"UPDATE batch SET unit_packaging_snapshot_v2={value} WHERE id=:id"),
                    {"id": batch_id},
                )
