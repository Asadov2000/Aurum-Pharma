"""Rehearse packaging rollback exclusively in the disposable test database."""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.service import IncomingService
from app.domains.suppliers.repository import SuppliersRepository
from app.domains.suppliers.service import SuppliersService
from tests.domains.inventory.test_unit_ledger import PACKAGING, seed_ledger_context
from tests.isolation.test_incoming_blister_migration import _migration as _blister_migration


def _migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/0163_incoming_unit_packaging_preparation.py"
    )
    spec = importlib.util.spec_from_file_location("incoming_packaging_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


async def test_packaging_migration_roundtrip_or_preserves_snapshots(
    maintenance_engine: AsyncEngine,
) -> None:
    migration = _migration()

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            nonempty = connection.scalar(text("""
                SELECT EXISTS(SELECT 1 FROM public.incoming_item
                  WHERE unit_packaging_preparation IS NOT NULL)
                  OR EXISTS(SELECT 1 FROM public.batch
                    WHERE unit_packaging_preparation IS NOT NULL)
            """))
            if nonempty:
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        migration.downgrade()
                assert error.value.orig.sqlstate == "55000"
                return
            # Rehearse old migration at its historical schema, then restore head.
            # The newer composition constraint references the v1 snapshot column.
            blister_migration = _blister_migration()
            blister_migration.downgrade()
            migration.downgrade()
            assert connection.scalar(text("""
                SELECT count(*) FROM information_schema.columns
                WHERE table_schema='public' AND table_name IN ('incoming_item','batch')
                  AND column_name='unit_packaging_preparation'
            """)) == 0
            migration.upgrade()
            assert connection.scalar(text("""
                SELECT count(*) FROM information_schema.columns
                WHERE table_schema='public' AND table_name IN ('incoming_item','batch')
                  AND column_name='unit_packaging_preparation'
                  AND is_nullable='YES' AND udt_name='jsonb'
            """)) == 2
            blister_migration.upgrade()
            assert connection.scalar(text("""
                SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal
                  AND tgname IN ('trg_guard_batch_unit_packaging_preparation',
                    'trg_validate_incoming_unit_packaging_preparation')
            """)) == 2

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_nonempty_packaging_downgrade_preserves_incoming_and_batch(
    db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        async with session.begin():
            assert str(await session.scalar(text("SELECT current_database()"))).endswith("_test")
            await session.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            context = await seed_ledger_context(session)
            supplier = await SuppliersService(SuppliersRepository(session)).create_supplier(
                tenant_id=context.tenant_id, fields={"name": "Packaging migration supplier"}
            )
            service = IncomingService(IncomingRepository(session))
            document = await service.create_document(
                tenant_id=context.tenant_id,
                fields={
                    "branch_id": context.branch_id,
                    "supplier_id": supplier.id,
                    "document_date": date.today(),
                },
            )
            item = await service.add_item(
                document.id,
                fields={
                    "catalog_id": context.catalog_id,
                    "expires_at": date.today() + timedelta(days=180),
                    "qty": Decimal("2"),
                    "purchase_price": Decimal("3.00"),
                    "sale_price": Decimal("5.00"),
                    "prepare_unit_sale": True,
                },
            )
            await service.accept(document.id)
            item_id, batch_id = item.id, item.created_batch_id
            assert batch_id is not None

    migration = _migration()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            with pytest.raises(DBAPIError) as error:
                async with connection.begin_nested():
                    await connection.run_sync(refuse)
            assert error.value.orig.sqlstate == "55000"
            for table, row_id in (("incoming_item", item_id), ("batch", batch_id)):
                assert (
                    await connection.scalar(
                        text(f"SELECT unit_packaging_preparation FROM public.{table} WHERE id=:id"),
                        {"id": row_id},
                    )
                    == PACKAGING.model_dump()
                )
        finally:
            await transaction.rollback()
