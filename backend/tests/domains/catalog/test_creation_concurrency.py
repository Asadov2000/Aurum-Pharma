"""Competing creation requests serialize on PostgreSQL and persist one complete result."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.errors import ConflictError
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.schemas import BarcodeCreate
from app.domains.catalog.service import CatalogService

PRODUCT_IDENTITY = {
    "brand_name": "Aspirin Cardio",
    "manufacturer": "Bayer",
    "form": "tablets",
    "dosage": "100 mg",
    "pack_size": "28",
}

type ImportFixture = tuple[async_sessionmaker[AsyncSession], UUID, UUID]


@pytest.mark.parametrize("different_payload", [False, True])
async def test_concurrent_create_retries_observe_committed_winner(
    committed_catalog_import: ImportFixture,
    maintenance_engine: AsyncEngine,
    different_payload: bool,
) -> None:
    if not (maintenance_engine.url.database or "").endswith("_test"):
        raise RuntimeError("Creation concurrency requires a disposable database")
    factory, tenant_id, _ = committed_catalog_import
    operation_id = uuid4()
    barcode = BarcodeCreate(code="4607013192829")
    contender_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    contender: asyncio.Task[UUID | None] | None = None

    async def second_request() -> UUID | None:
        try:
            async with factory.begin() as session:
                pid = int((await session.execute(text("SELECT pg_backend_pid()"))).scalar_one())
                contender_pid.set_result(pid)
                result = await CatalogService(CatalogRepository(session)).create_item(
                    tenant_id=tenant_id,
                    fields={"brand_name": "Other" if different_payload else "Created"},
                    operation_id=operation_id,
                    initial_barcode=barcode,
                )
                return result.id
        except ConflictError as error:
            assert different_payload
            assert error.details == {"reason": "operation_payload_mismatch"}
            return None

    try:
        async with factory.begin() as first:
            repo = CatalogRepository(first)
            await repo.lock_creation_operation(tenant_id=tenant_id, operation_id=operation_id)
            first_pid = int((await first.execute(text("SELECT pg_backend_pid()"))).scalar_one())
            contender = asyncio.create_task(second_request())
            async with asyncio.timeout(10):
                blocked_pid = await contender_pid
                while not await first.scalar(
                    text("SELECT :first = ANY(pg_blocking_pids(:second))"),
                    {"first": first_pid, "second": blocked_pid},
                ):
                    await asyncio.sleep(0.01)
                winner = await CatalogService(repo).create_item(
                    tenant_id=tenant_id,
                    fields={"brand_name": "Created"},
                    operation_id=operation_id,
                    initial_barcode=barcode,
                )
                winner_id = winner.id
                await first.commit()
                result_id = await contender
        assert result_id == (None if different_payload else winner_id)
        async with factory() as observer:
            snapshot = (
                await observer.execute(
                    text(
                        "SELECT (SELECT count(*) FROM tenant_catalog WHERE tenant_id=:tenant), "
                        "(SELECT count(*) FROM barcode "
                        "WHERE tenant_id=:tenant AND catalog_id=:id), "
                        "(SELECT count(*) FROM audit_log WHERE tenant_id=:tenant "
                        "AND table_name IN ('tenant_catalog','barcode'))"
                    ),
                    {"tenant": tenant_id, "id": winner_id},
                )
            ).one()
            # 0010 audits tenant_catalog, while barcode has no audit trigger.
            # A second creation or any replay UPDATE would add another entry.
            assert tuple(snapshot) == (1, 1, 1)
    finally:
        if contender is not None and not contender.done():
            contender.cancel()
            await asyncio.gather(contender, return_exceptions=True)
        # The generated fixture lives only in a disposable database. Temporarily
        # suspend this guard inside the same maintenance transaction as cleanup;
        # ordinary application roles cannot erase creation identity this way.
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("ALTER TABLE tenant_catalog DISABLE TRIGGER guard_catalog_creation_metadata")
            )
            await connection.execute(
                text(
                    "DELETE FROM tenant_catalog WHERE tenant_id=:tenant AND operation_id=:operation"
                ),
                {"tenant": tenant_id, "operation": operation_id},
            )
            await connection.execute(
                text("ALTER TABLE tenant_catalog ENABLE TRIGGER guard_catalog_creation_metadata")
            )


async def test_concurrent_distinct_operations_detect_the_winner(
    committed_catalog_import: ImportFixture,
    maintenance_engine: AsyncEngine,
) -> None:
    if not (maintenance_engine.url.database or "").endswith("_test"):
        raise RuntimeError("Creation concurrency requires a disposable database")
    factory, tenant_id, _ = committed_catalog_import
    winner_operation, contender_operation = uuid4(), uuid4()
    contender_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    contender: asyncio.Task[UUID | None] | None = None
    identity = tuple(value.lower() for value in PRODUCT_IDENTITY.values())

    async def second_request() -> UUID | None:
        async with factory.begin() as session:
            pid = int((await session.execute(text("SELECT pg_backend_pid()"))).scalar_one())
            contender_pid.set_result(pid)
            try:
                item = await CatalogService(CatalogRepository(session)).create_item(
                    tenant_id=tenant_id,
                    fields=PRODUCT_IDENTITY,
                    operation_id=contender_operation,
                )
            except ConflictError as error:
                assert error.details["reason"] == "catalog_identity_match"
                return UUID(str(error.details["catalog_id"]))
            return item.id

    try:
        async with factory.begin() as first:
            repo = CatalogRepository(first)
            await repo.lock_creation_identity(tenant_id=tenant_id, identity=identity)
            first_pid = int((await first.execute(text("SELECT pg_backend_pid()"))).scalar_one())
            contender = asyncio.create_task(second_request())
            async with asyncio.timeout(10):
                blocked_pid = await contender_pid
                while not await first.scalar(
                    text("SELECT :first = ANY(pg_blocking_pids(:second))"),
                    {"first": first_pid, "second": blocked_pid},
                ):
                    await asyncio.sleep(0.01)
                winner = await CatalogService(repo).create_item(
                    tenant_id=tenant_id,
                    fields=PRODUCT_IDENTITY,
                    operation_id=winner_operation,
                )
                winner_id = winner.id
                await first.commit()
                assert await contender == winner_id
        async with factory() as observer:
            total = await observer.scalar(
                text(
                    "SELECT count(*) FROM tenant_catalog WHERE tenant_id=:tenant "
                    "AND operation_id IN (:winner, :contender)"
                ),
                {"tenant": tenant_id, "winner": winner_operation, "contender": contender_operation},
            )
            assert total == 1
    finally:
        if contender is not None and not contender.done():
            contender.cancel()
            await asyncio.gather(contender, return_exceptions=True)
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("ALTER TABLE tenant_catalog DISABLE TRIGGER guard_catalog_creation_metadata")
            )
            await connection.execute(
                text(
                    "DELETE FROM tenant_catalog WHERE tenant_id=:tenant AND operation_id=:operation"
                ),
                {"tenant": tenant_id, "operation": winner_operation},
            )
            await connection.execute(
                text("ALTER TABLE tenant_catalog ENABLE TRIGGER guard_catalog_creation_metadata")
            )
