"""Internal prepared acceptance creates immutable source stock without legacy stock."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.errors import BusinessRuleError, ConflictError, PermissionDeniedError
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.incoming import service as incoming_service_module
from app.domains.incoming.models import IncomingDocument
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.service import IncomingService
from app.domains.inventory.unit_ledger_repository import UnitLedgerRepository
from app.domains.inventory.unit_ledger_service import PreparedUnitLedgerService
from app.domains.suppliers.repository import SuppliersRepository
from app.domains.suppliers.service import SuppliersService
from tests.domains.inventory.test_unit_ledger import PACKAGING, LedgerContext, seed_ledger_context
from tests.domains.pos.test_unit_line import post


@dataclass(frozen=True)
class IncomingContext:
    stock: LedgerContext
    document_id: UUID
    item_ids: tuple[UUID, ...]
    actor_id: UUID


@dataclass(frozen=True)
class ReceivingRuntime:
    factory: async_sessionmaker[AsyncSession]
    stock: LedgerContext
    actor_id: UUID


async def seed_receiving_actor(
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    stock: LedgerContext,
    *,
    permissions: tuple[str, ...] | None = None,
) -> UUID:
    actor_id, membership_id, role_id = uuid4(), uuid4(), uuid4()
    async with db_engine.begin() as connection:
        assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
        await connection.execute(text("SELECT set_config('app.support_session','true',true)"))
        await connection.execute(
            text(
                "INSERT INTO app_user (id,email,full_name,home_tenant_id,status,activated_at) "
                "VALUES (:id,:email,'Receiving operator',:tenant,'active',now())"
            ),
            {
                "id": actor_id,
                "email": f"receiving-{actor_id}@aurum.test",
                "tenant": stock.tenant_id,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO tenant_membership "
                "(id,tenant_id,user_id,full_name,status,activated_at) "
                "VALUES (:membership,:tenant,:actor,'Receiving operator','active',now())"
            ),
            {"membership": membership_id, "tenant": stock.tenant_id, "actor": actor_id},
        )
        await connection.execute(
            text(
                "INSERT INTO role (id,tenant_id,name,level,is_system,is_active,is_protected) "
                "VALUES (:role,:tenant,:name,4,false,true,false)"
            ),
            {"role": role_id, "tenant": stock.tenant_id, "name": f"Receiving tests {role_id}"},
        )
        if permissions is None:
            await connection.execute(
                text(
                    "INSERT INTO role_permission (role_id,permission_code) "
                    "SELECT :role,code FROM permission"
                ),
                {"role": role_id},
            )
        else:
            for permission in permissions:
                await connection.execute(
                    text(
                        "INSERT INTO role_permission (role_id,permission_code) "
                        "VALUES (:role,:permission)"
                    ),
                    {"role": role_id, "permission": permission},
                )
    async with maintenance_engine.begin() as owner:
        await owner.execute(
            text(
                "INSERT INTO access_role_version "
                "(id,role_id,tenant_id,version,name,description,status,"
                "creation_xid,published_at,created_by) "
                "SELECT gen_random_uuid(),id,tenant_id,version,name,description,'published',"
                "txid_current(),statement_timestamp(),created_by FROM role WHERE id=:role"
            ),
            {"role": role_id},
        )
        await owner.execute(
            text(
                "INSERT INTO access_role_version_permission (role_version_id,permission_code) "
                "SELECT version.id,permission.permission_code FROM access_role_version version "
                "JOIN role_permission permission ON permission.role_id=version.role_id "
                "WHERE version.role_id=:role"
            ),
            {"role": role_id},
        )
    async with db_engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.support_session','true',true)"))
        await connection.execute(
            text(
                "INSERT INTO user_assignment "
                "(user_id,tenant_id,membership_id,branch_id,role_id,is_active) "
                "VALUES (:actor,:tenant,:membership,:branch,:role,true)"
            ),
            {
                "actor": actor_id,
                "tenant": stock.tenant_id,
                "membership": membership_id,
                "branch": stock.branch_id,
                "role": role_id,
            },
        )
    return actor_id


async def set_receiving_context(
    session: AsyncSession, stock: LedgerContext, actor_id: UUID
) -> None:
    await session.execute(
        text(
            "SELECT set_config('app.tenant_id',:tenant,true),set_config('app.user_id',:actor,true)"
        ),
        {"tenant": str(stock.tenant_id), "actor": str(actor_id)},
    )
    assert await session.scalar(text("SELECT current_user")) == "aurum_app"


@pytest_asyncio.fixture
async def db_session(
    db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> AsyncIterator[AsyncSession]:
    """Production app identity; seed only fresh synthetic identities in the test DB."""
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            assert str(await support.scalar(text("SELECT current_database()"))).endswith("_test")
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            stock = await seed_ledger_context(support)
    actor_id = await seed_receiving_actor(db_engine, maintenance_engine, stock)
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            session.info["receiving_runtime"] = ReceivingRuntime(factory, stock, actor_id)
            await set_receiving_context(session, stock, actor_id)
            yield session
            await session.rollback()
    finally:
        await engine.dispose()


async def seed_prepared_incoming(
    session: AsyncSession,
    *,
    context: LedgerContext | None = None,
    catalogs: tuple[UUID, ...] | None = None,
    cost: Decimal = Decimal("10.00"),
) -> IncomingContext:
    runtime = cast(ReceivingRuntime, session.info["receiving_runtime"])
    stock = context or runtime.stock
    actor_id = runtime.actor_id
    supplier = await SuppliersService(SuppliersRepository(session)).create_supplier(
        tenant_id=stock.tenant_id, fields={"name": f"Prepared supplier {uuid4().hex}"}
    )
    service = IncomingService(IncomingRepository(session))
    document = await service.create_document(
        tenant_id=stock.tenant_id,
        fields={
            "branch_id": stock.branch_id,
            "supplier_id": supplier.id,
            "document_date": date.today(),
        },
    )
    identifiers: list[UUID] = []
    for catalog_id in catalogs or (stock.catalog_id,):
        item = await service.add_item(
            document.id,
            fields={
                "catalog_id": catalog_id,
                "expires_at": date.today() + timedelta(days=180),
                "qty": Decimal("2"),
                "purchase_price": cost,
                "sale_price": Decimal("25.00"),
                "prepare_unit_sale": True,
            },
        )
        identifiers.append(item.id)
    return IncomingContext(stock, document.id, tuple(identifiers), actor_id)


async def accept_prepared(session: AsyncSession, incoming: IncomingContext) -> IncomingDocument:
    return await IncomingService(IncomingRepository(session)).accept_prepared(
        incoming.document_id,
        actor_id=incoming.actor_id,
        allowed_branch_ids={incoming.stock.branch_id},
    )


async def assert_draft_without_stock(session: AsyncSession, incoming: IncomingContext) -> None:
    assert (
        await session.scalar(
            text("SELECT status FROM incoming_document WHERE id=:id"), {"id": incoming.document_id}
        )
        == "draft"
    )
    for table in (
        "unit_stock_lot",
        "unit_stock_operation",
        "unit_stock_entry",
        "batch",
        "batch_movement",
    ):
        assert (
            await session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": incoming.stock.tenant_id},
            )
            == 0
        )
    assert (
        await session.scalar(
            text(
                "SELECT count(*) FROM incoming_item WHERE document_id=:id "
                "AND created_unit_lot_id IS NOT NULL"
            ),
            {"id": incoming.document_id},
        )
        == 0
    )


@pytest.mark.parametrize("cost", [Decimal("10.00"), Decimal("0.00")])
async def test_prepared_acceptance_copies_saved_source_and_creates_no_legacy_stock(
    db_session: AsyncSession, cost: Decimal
) -> None:
    incoming = await seed_prepared_incoming(db_session, cost=cost)
    document = await accept_prepared(db_session, incoming)
    assert document.status == "accepted"
    item = (
        await db_session.execute(
            text("SELECT created_unit_lot_id,created_batch_id FROM incoming_item WHERE id=:id"),
            {"id": incoming.item_ids[0]},
        )
    ).one()
    assert item.created_unit_lot_id is not None and item.created_batch_id is None
    lot = (
        await db_session.execute(
            text(
                "SELECT tenant_id,branch_id,catalog_id,closed_packs,initial_closed_packs,"
                "purchase_cost_per_pack,unit,units_per_pack,dispense_step,expires_at "
                "FROM unit_stock_lot WHERE id=:id"
            ),
            {"id": item.created_unit_lot_id},
        )
    ).one()
    assert (lot.tenant_id, lot.branch_id, lot.catalog_id) == (
        incoming.stock.tenant_id,
        incoming.stock.branch_id,
        incoming.stock.catalog_id,
    )
    assert lot.closed_packs == lot.initial_closed_packs == 2
    assert lot.purchase_cost_per_pack == cost
    assert (lot.unit, lot.units_per_pack, lot.dispense_step) == ("tablet", 30, 1)
    assert lot.expires_at == date.today() + timedelta(days=180)
    for table in ("batch", "batch_movement"):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": incoming.stock.tenant_id},
            )
            == 0
        )
    result = await post(db_session, incoming.stock, quantity=3)
    assert result.snapshot.original_cost_amount == (Decimal("1.00") if cost else Decimal("0.00"))
    assert result.cost_slices[0].lot_id == item.created_unit_lot_id


async def test_acceptance_uses_saved_packaging_after_current_catalog_changes(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_prepared_incoming(db_session)
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET unit_sale_preparation="
            "jsonb_set(unit_sale_preparation,'{units_per_pack}','60') WHERE id=:id"
        ),
        {"id": incoming.stock.catalog_id},
    )
    await accept_prepared(db_session, incoming)
    assert (
        await db_session.scalar(
            text("SELECT units_per_pack FROM unit_stock_lot WHERE tenant_id=:tenant"),
            {"tenant": incoming.stock.tenant_id},
        )
        == 30
    )


async def test_invalid_second_item_rolls_back_first_item_and_header(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_prepared_incoming(db_session)
    service = IncomingService(IncomingRepository(db_session))
    await service.add_item(
        incoming.document_id,
        fields={
            "catalog_id": incoming.stock.catalog_id,
            "expires_at": date.today() + timedelta(days=180),
            "qty": Decimal("1"),
            "purchase_price": Decimal("10.00"),
            "sale_price": Decimal("25.00"),
            "prepare_unit_sale": False,
        },
    )
    with pytest.raises(BusinessRuleError):
        await accept_prepared(db_session, incoming)
    await assert_draft_without_stock(db_session, incoming)


async def test_outer_rollback_restores_draft_source_and_can_accept_again(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_prepared_incoming(db_session)
    outer = await db_session.begin_nested()
    await accept_prepared(db_session, incoming)
    await outer.rollback()
    await assert_draft_without_stock(db_session, incoming)
    assert (await accept_prepared(db_session, incoming)).status == "accepted"


async def test_replay_ignores_changed_current_prices_but_rechecks_allowed_branch(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    incoming = await seed_prepared_incoming(db_session)
    first = await accept_prepared(db_session, incoming)
    first_accepted_at = first.accepted_at
    assert first_accepted_at is not None
    original = await db_session.scalar(
        text("SELECT created_unit_lot_id FROM incoming_item WHERE id=:id"),
        {"id": incoming.item_ids[0]},
    )
    await db_session.execute(
        text("UPDATE tenant_catalog SET base_price=99.00 WHERE id=:id"),
        {"id": incoming.stock.catalog_id},
    )
    monkeypatch.setattr(
        incoming_service_module, "utc_now", lambda: first_accepted_at + timedelta(days=365)
    )
    repeated = await accept_prepared(db_session, incoming)
    assert repeated.accepted_at == first_accepted_at
    assert (
        await db_session.scalar(
            text("SELECT created_unit_lot_id FROM incoming_item WHERE id=:id"),
            {"id": incoming.item_ids[0]},
        )
        == original
    )
    with pytest.raises(PermissionDeniedError):
        await IncomingService(IncomingRepository(db_session)).accept_prepared(
            incoming.document_id, actor_id=incoming.actor_id, allowed_branch_ids=set()
        )


async def test_legacy_acceptance_with_prepared_flag_still_creates_legacy_batch_and_blocks_unit_mode(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_prepared_incoming(db_session)
    service = IncomingService(IncomingRepository(db_session))
    await service.accept(incoming.document_id)
    assert (
        await db_session.scalar(
            text("SELECT created_batch_id FROM incoming_item WHERE id=:id"),
            {"id": incoming.item_ids[0]},
        )
        is not None
    )
    assert (
        await db_session.scalar(
            text("SELECT created_unit_lot_id FROM incoming_item WHERE id=:id"),
            {"id": incoming.item_ids[0]},
        )
        is None
    )
    with pytest.raises(ConflictError):
        await accept_prepared(db_session, incoming)


async def test_prepared_acceptance_cannot_be_replayed_through_legacy_mode(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_prepared_incoming(db_session)
    await accept_prepared(db_session, incoming)
    with pytest.raises(ConflictError):
        await IncomingService(IncomingRepository(db_session)).accept(incoming.document_id)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM batch WHERE tenant_id=:tenant"),
            {"tenant": incoming.stock.tenant_id},
        )
        == 0
    )


@pytest_asyncio.fixture
async def committed_incoming(
    db_session: AsyncSession,
) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], IncomingContext]]:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    incoming = await seed_prepared_incoming(db_session)
    await db_session.commit()
    yield runtime.factory, incoming


async def concurrent_accept(
    factory: async_sessionmaker[AsyncSession], incoming: IncomingContext
) -> UUID:
    async with factory.begin() as session:
        await set_receiving_context(session, incoming.stock, incoming.actor_id)
        await accept_prepared(session, incoming)
        lot_id = await session.scalar(
            text("SELECT created_unit_lot_id FROM incoming_item WHERE id=:id"),
            {"id": incoming.item_ids[0]},
        )
        assert isinstance(lot_id, UUID)
        return lot_id


async def test_simultaneous_document_acceptance_initializes_stock_only_once(
    committed_incoming: tuple[async_sessionmaker[AsyncSession], IncomingContext],
) -> None:
    factory, incoming = committed_incoming
    results = await asyncio.wait_for(
        asyncio.gather(*(concurrent_accept(factory, incoming) for _ in range(4))), timeout=15
    )
    assert len(set(results)) == 1
    async with factory.begin() as session:
        await set_receiving_context(session, incoming.stock, incoming.actor_id)
        assert (
            await session.scalar(
                text("SELECT count(*) FROM unit_stock_lot WHERE tenant_id=:tenant"),
                {"tenant": incoming.stock.tenant_id},
            )
            == 1
        )
        assert (
            await session.scalar(
                text("SELECT sum(closed_packs) FROM unit_stock_lot WHERE tenant_id=:tenant"),
                {"tenant": incoming.stock.tenant_id},
            )
            == 2
        )


async def test_acceptance_rejects_preexisting_raw_initialization_instead_of_binding_stock(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_prepared_incoming(db_session)
    operation_id = uuid5(
        NAMESPACE_URL, f"aurum-unit-incoming:{incoming.stock.tenant_id}:{incoming.item_ids[0]}"
    )
    await PreparedUnitLedgerService(UnitLedgerRepository(db_session)).prepare_lot(
        tenant_id=incoming.stock.tenant_id,
        branch_id=incoming.stock.branch_id,
        catalog_id=incoming.stock.catalog_id,
        operation_id=operation_id,
        packaging=PACKAGING,
        closed_packs=2,
        expires_at=date.today() + timedelta(days=180),
        allowed_branch_ids={incoming.stock.branch_id},
        purchase_cost_per_pack=Decimal("10.00"),
    )
    with pytest.raises(ConflictError):
        await accept_prepared(db_session, incoming)
    assert (
        await db_session.scalar(
            text("SELECT status FROM incoming_document WHERE id=:id"), {"id": incoming.document_id}
        )
        == "draft"
    )
    assert (
        await db_session.scalar(
            text("SELECT created_unit_lot_id FROM incoming_item WHERE id=:id"),
            {"id": incoming.item_ids[0]},
        )
        is None
    )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM unit_stock_lot WHERE tenant_id=:tenant"),
            {"tenant": incoming.stock.tenant_id},
        )
        == 1
    )


async def test_two_documents_with_opposite_catalog_order_complete_without_deadlock(
    db_session: AsyncSession,
) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    second_catalog = await CatalogService(CatalogRepository(db_session)).create_item(
        tenant_id=runtime.stock.tenant_id,
        fields={
            "brand_name": "Second prepared item",
            "unit_sale_preparation": {
                **PACKAGING.model_dump(),
                "state": "prepared",
                "unit_price": "1.00",
            },
        },
    )
    first = await seed_prepared_incoming(
        db_session, catalogs=(runtime.stock.catalog_id, second_catalog.id)
    )
    second = await seed_prepared_incoming(
        db_session, catalogs=(second_catalog.id, runtime.stock.catalog_id)
    )
    await db_session.commit()
    results = await asyncio.wait_for(
        asyncio.gather(
            concurrent_accept(runtime.factory, first), concurrent_accept(runtime.factory, second)
        ),
        timeout=15,
    )
    assert len(set(results)) == 2
    await set_receiving_context(db_session, runtime.stock, runtime.actor_id)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM unit_stock_lot WHERE tenant_id=:tenant"),
            {"tenant": runtime.stock.tenant_id},
        )
        == 4
    )
    assert (
        await db_session.scalar(
            text("SELECT sum(closed_packs) FROM unit_stock_lot WHERE tenant_id=:tenant"),
            {"tenant": runtime.stock.tenant_id},
        )
        == 8
    )


async def test_bound_source_cannot_be_changed_rebound_or_deleted(db_session: AsyncSession) -> None:
    incoming = await seed_prepared_incoming(db_session)
    await accept_prepared(db_session, incoming)
    for statement in (
        "UPDATE incoming_item SET created_unit_lot_id=:foreign WHERE id=:id",
        "UPDATE incoming_item SET purchase_price=99 WHERE id=:id",
        "UPDATE incoming_item SET qty=3 WHERE id=:id",
        "UPDATE incoming_item SET unit_packaging_preparation=NULL WHERE id=:id",
        "DELETE FROM incoming_item WHERE id=:id",
    ):
        with pytest.raises(DBAPIError):
            async with db_session.begin_nested():
                await db_session.execute(
                    text(statement), {"id": incoming.item_ids[0], "foreign": uuid4()}
                )


async def test_second_source_cannot_reuse_an_existing_lot_binding(db_session: AsyncSession) -> None:
    first = await seed_prepared_incoming(db_session)
    second = await seed_prepared_incoming(db_session)
    await accept_prepared(db_session, first)
    lot_id = await db_session.scalar(
        text("SELECT created_unit_lot_id FROM incoming_item WHERE id=:id"),
        {"id": first.item_ids[0]},
    )
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE incoming_item SET created_unit_lot_id=:lot WHERE id=:id"),
                {"lot": lot_id, "id": second.item_ids[0]},
            )
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert (
        await db_session.scalar(
            text("SELECT created_unit_lot_id FROM incoming_item WHERE id=:id"),
            {"id": second.item_ids[0]},
        )
        is None
    )


async def test_receiver_actor_and_branch_context_are_required(db_session: AsyncSession) -> None:
    incoming = await seed_prepared_incoming(db_session)
    with pytest.raises(PermissionDeniedError):
        await IncomingService(IncomingRepository(db_session)).accept_prepared(
            incoming.document_id, actor_id=uuid4(), allowed_branch_ids={incoming.stock.branch_id}
        )
    await assert_draft_without_stock(db_session, incoming)


async def test_private_preparation_cannot_commit_without_accepting_whole_document(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_prepared_incoming(db_session)
    ledger = PreparedUnitLedgerService(UnitLedgerRepository(db_session))
    with pytest.raises(DBAPIError, match="unit_incoming_document_incomplete"):
        async with db_session.begin_nested():
            await ledger._claim_incoming_lot(
                incoming.item_ids[0],
                actor_id=incoming.actor_id,
                allowed_branch_ids={incoming.stock.branch_id},
            )
            await ledger._prepare_incoming_lot(
                incoming.item_ids[0],
                actor_id=incoming.actor_id,
                allowed_branch_ids={incoming.stock.branch_id},
            )
            assert (
                await db_session.scalar(
                    text("SELECT created_unit_lot_id FROM incoming_item WHERE id=:id"),
                    {"id": incoming.item_ids[0]},
                )
                is not None
            )
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await assert_draft_without_stock(db_session, incoming)


async def test_raw_binding_cannot_attach_an_already_initialized_lot(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_prepared_incoming(db_session)
    operation_id = uuid5(
        NAMESPACE_URL,
        f"aurum-unit-incoming:{incoming.stock.tenant_id}:{incoming.item_ids[0]}",
    )
    prepared = await PreparedUnitLedgerService(UnitLedgerRepository(db_session)).prepare_lot(
        tenant_id=incoming.stock.tenant_id,
        branch_id=incoming.stock.branch_id,
        catalog_id=incoming.stock.catalog_id,
        operation_id=operation_id,
        packaging=PACKAGING,
        closed_packs=2,
        expires_at=date.today() + timedelta(days=180),
        allowed_branch_ids={incoming.stock.branch_id},
        purchase_cost_per_pack=Decimal("10.00"),
    )
    with pytest.raises(DBAPIError, match="unit_incoming_source_binding_invalid"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE incoming_item SET created_unit_lot_id=:lot WHERE id=:id"),
                {"lot": prepared.lot_id, "id": incoming.item_ids[0]},
            )
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert (
        await db_session.scalar(
            text("SELECT created_unit_lot_id FROM incoming_item WHERE id=:id"),
            {"id": incoming.item_ids[0]},
        )
        is None
    )
