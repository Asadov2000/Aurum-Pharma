"""Committed two-session acceptance checks, restricted to disposable databases."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.catalog.models import TenantCatalog
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.foundation.models import Branch
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.incoming.models import IncomingDocument
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.service import IncomingService
from app.domains.inventory.models import Batch, BatchMovement
from app.domains.suppliers.models import Supplier
from app.domains.suppliers.repository import SuppliersRepository


@dataclass(frozen=True)
class CommittedIncoming:
    tenant_id: UUID
    branch_id: UUID
    catalog_id: UUID
    supplier_id: UUID
    document_ids: tuple[UUID, UUID]


type IncomingFixture = tuple[async_sessionmaker[AsyncSession], CommittedIncoming]


@pytest_asyncio.fixture
async def committed_incoming(
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> AsyncIterator[IncomingFixture]:
    for engine in (db_engine, maintenance_engine):
        if not (engine.url.database or "").endswith("_test"):
            raise RuntimeError("Acceptance concurrency tests require a disposable '_test' database")

    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory.begin() as session:
        foundation = FoundationService(FoundationRepository(session))
        tenant = await foundation.create_tenant(
            payload={
                "name": "Incoming concurrency",
                "contact_email": f"incoming-{uuid4().hex}@example.invalid",
            }
        )
        branch = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "Main"})
        await foundation.create_branch(tenant_id=tenant.id, fields={"name": "Backup"})
        catalog = await CatalogService(CatalogRepository(session)).create_item(
            tenant_id=tenant.id, fields={"brand_name": "Incoming concurrency item"}
        )
        supplier = await SuppliersRepository(session).create_supplier(
            tenant_id=tenant.id, name="Incoming concurrency supplier"
        )
        service = IncomingService(IncomingRepository(session))
        document_ids: list[UUID] = []
        for _ in range(2):
            doc = await service.create_document(
                tenant_id=tenant.id,
                fields={
                    "branch_id": branch.id,
                    "supplier_id": supplier.id,
                    "document_date": date.today(),
                },
            )
            await service.add_item(
                doc.id,
                fields={
                    "catalog_id": catalog.id,
                    "expires_at": date.today() + timedelta(days=365),
                    "qty": Decimal("3"),
                    "purchase_price": Decimal("5.00"),
                    "sale_price": Decimal("8.00"),
                },
            )
            document_ids.append(doc.id)
        context = CommittedIncoming(
            tenant.id, branch.id, catalog.id, supplier.id, (document_ids[0], document_ids[1])
        )

    try:
        yield factory, context
    finally:
        guarded_tables = (
            ("incoming_item", "trg_guard_incoming_item_lifecycle"),
            ("incoming_document", "trg_guard_incoming_document_lifecycle"),
            ("incoming_document", "trg_incoming_document_writer_guard"),
            ("batch_movement", "trg_guard_batch_movement_immutability"),
            ("batch", "trg_batch_writer_guard"),
        )
        # Keep all DISABLE/DELETE/ENABLE statements atomic. Writer guards inspect
        # SESSION_USER, so SET LOCAL ROLE cannot authorize owner-scoped deletes.
        async with maintenance_engine.begin() as connection:
            for table, trigger in guarded_tables:
                await connection.execute(
                    text(f"ALTER TABLE public.{table} DISABLE TRIGGER {trigger}")
                )
            for table in ("incoming_item", "incoming_document", "batch_movement", "batch"):
                await connection.execute(
                    text(f"DELETE FROM public.{table} WHERE tenant_id = :tenant_id"),
                    {"tenant_id": context.tenant_id},
                )
            await connection.execute(
                text("DELETE FROM public.audit_log WHERE tenant_id = :tenant_id"),
                {"tenant_id": context.tenant_id},
            )
            for table, trigger in reversed(guarded_tables):
                await connection.execute(
                    text(f"ALTER TABLE public.{table} ENABLE TRIGGER {trigger}")
                )
        # Cascaded writer-ledger cleanup requires the support login. All guards
        # are already restored; interruption here can only leave fixture data.
        async with db_engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            await connection.execute(
                text("DELETE FROM public.tenant WHERE id = :tenant_id"),
                {"tenant_id": context.tenant_id},
            )


async def _backend_pid(session: AsyncSession) -> int:
    return int((await session.execute(text("SELECT pg_backend_pid()"))).scalar_one())


async def _wait_until_blocked(
    observer: AsyncSession, *, blocked_pid: int, blocker_pid: int
) -> None:
    # Observe the actual lock wait instead of assuming task scheduling after a sleep.
    async with asyncio.timeout(10):
        while not await observer.scalar(
            text("SELECT :blocker_pid = ANY(pg_blocking_pids(:blocked_pid))"),
            {"blocked_pid": blocked_pid, "blocker_pid": blocker_pid},
        ):
            await asyncio.sleep(0)


async def _assert_effects(
    session: AsyncSession, context: CommittedIncoming, *, accepted_count: int
) -> None:
    batches = list(
        (await session.scalars(select(Batch).where(Batch.tenant_id == context.tenant_id))).all()
    )
    assert len(batches) == accepted_count
    assert all(batch.qty_initial == batch.qty_remaining == Decimal("3") for batch in batches)
    movements = list(
        (
            await session.scalars(
                select(BatchMovement).where(BatchMovement.tenant_id == context.tenant_id)
            )
        ).all()
    )
    assert len(movements) == accepted_count
    assert all(
        movement.movement_type == "incoming" and movement.qty_delta == Decimal("3")
        for movement in movements
    )
    count = await session.scalar(
        select(func.count())
        .select_from(IncomingDocument)
        .where(
            IncomingDocument.tenant_id == context.tenant_id,
            IncomingDocument.status == "accepted",
        )
    )
    assert count == accepted_count


async def test_accept_before_branch_deactivation_has_no_fk_deadlock(
    committed_incoming: IncomingFixture,
) -> None:
    factory, context = committed_incoming
    async with asyncio.timeout(15):
        async with factory.begin() as accepting, factory.begin() as deactivating:
            repo = IncomingRepository(accepting)
            await repo.get_document_for_update(context.document_ids[0])
            await repo.get_acceptance_references_for_update(
                tenant_id=context.tenant_id,
                branch_id=context.branch_id,
                supplier_id=context.supplier_id,
                catalog_ids={context.catalog_id},
            )
            accepting_pid = await _backend_pid(accepting)
            deactivating_pid = await _backend_pid(deactivating)
            async with asyncio.TaskGroup() as tasks:
                deactivate = tasks.create_task(
                    FoundationService(FoundationRepository(deactivating)).update_branch(
                        context.branch_id, fields={"is_active": False}
                    )
                )
                await _wait_until_blocked(
                    accepting, blocked_pid=deactivating_pid, blocker_pid=accepting_pid
                )
                # The old branch -> tenant order deadlocked at the batch FK here.
                doc = await IncomingService(repo).accept(context.document_ids[0])
                assert doc.status == "accepted"
                await accepting.commit()
                assert not (await deactivate).is_active

    async with factory() as session:
        await _assert_effects(session, context, accepted_count=1)
        branch = await session.get(Branch, context.branch_id)
        assert branch is not None and not branch.is_active
        retried = await IncomingService(IncomingRepository(session)).accept(context.document_ids[0])
        assert retried.status == "accepted"
        await _assert_effects(session, context, accepted_count=1)


async def test_branch_deactivation_before_acceptance_has_no_deadlock(
    committed_incoming: IncomingFixture,
) -> None:
    factory, context = committed_incoming
    async with asyncio.timeout(15):
        async with factory.begin() as deactivating, factory.begin() as accepting:
            foundation = FoundationRepository(deactivating)
            await foundation.get_tenant_for_update(context.tenant_id)
            accepting_pid = await _backend_pid(accepting)
            deactivating_pid = await _backend_pid(deactivating)

            async def accept_inactive_branch() -> None:
                with pytest.raises(BusinessRuleError, match="Branch is inactive"):
                    await IncomingService(IncomingRepository(accepting)).accept(
                        context.document_ids[0]
                    )

            async with asyncio.TaskGroup() as tasks:
                accept = tasks.create_task(accept_inactive_branch())
                await _wait_until_blocked(
                    deactivating, blocked_pid=accepting_pid, blocker_pid=deactivating_pid
                )
                branch = await FoundationService(foundation).update_branch(
                    context.branch_id, fields={"is_active": False}
                )
                assert not branch.is_active
                await deactivating.commit()
                await accept

    async with factory() as session:
        await _assert_effects(session, context, accepted_count=0)
        doc = await session.get(IncomingDocument, context.document_ids[0])
        assert doc is not None and doc.status == "draft"


async def test_concurrent_accept_retry_refreshes_document_and_has_one_effect(
    committed_incoming: IncomingFixture,
) -> None:
    factory, context = committed_incoming
    async with asyncio.timeout(15):
        async with factory.begin() as first, factory.begin() as second:
            cached = await second.get(IncomingDocument, context.document_ids[0])
            assert cached is not None and cached.status == "draft"
            first_pid = await _backend_pid(first)
            second_pid = await _backend_pid(second)
            accepted = await IncomingService(IncomingRepository(first)).accept(cached.id)
            async with asyncio.TaskGroup() as tasks:
                retry = tasks.create_task(
                    IncomingService(IncomingRepository(second)).accept(cached.id)
                )
                await _wait_until_blocked(first, blocked_pid=second_pid, blocker_pid=first_pid)
                await first.commit()
                retried = await retry
                assert retried is cached
                assert retried.id == accepted.id and retried.status == "accepted"

    async with factory() as session:
        await _assert_effects(session, context, accepted_count=1)


async def test_distinct_documents_share_reference_locks_and_batch_fk_locks(
    committed_incoming: IncomingFixture,
) -> None:
    factory, context = committed_incoming
    async with asyncio.timeout(10):
        async with factory.begin() as first, factory.begin() as second:
            await IncomingService(IncomingRepository(first)).accept(context.document_ids[0])
            # The second transaction must finish acceptance before the first commits.
            doc = await IncomingService(IncomingRepository(second)).accept(context.document_ids[1])
            assert doc.status == "accepted"

    async with factory() as session:
        await _assert_effects(session, context, accepted_count=2)


@pytest.mark.parametrize(
    "reference", ["branch", "supplier", "catalog_inactive", "catalog_archived"]
)
async def test_committed_deactivation_refreshes_stale_reference_identity_map(
    committed_incoming: IncomingFixture,
    reference: Literal["branch", "supplier", "catalog_inactive", "catalog_archived"],
) -> None:
    factory, context = committed_incoming
    async with factory.begin() as reader:
        cached_branch = await reader.get(Branch, context.branch_id)
        cached_supplier = await reader.get(Supplier, context.supplier_id)
        cached_catalog = await reader.get(TenantCatalog, context.catalog_id)
        assert cached_branch is not None and cached_branch.is_active
        assert cached_supplier is not None and cached_supplier.is_active
        assert cached_catalog is not None and cached_catalog.is_active
        assert cached_catalog.deleted_at is None

        async with factory.begin() as writer:
            if reference == "branch":
                await FoundationService(FoundationRepository(writer)).update_branch(
                    context.branch_id, fields={"is_active": False}
                )
            elif reference == "supplier":
                supplier = await writer.get(Supplier, context.supplier_id)
                assert supplier is not None
                supplier.is_active = False
            else:
                catalog = await writer.get(TenantCatalog, context.catalog_id)
                assert catalog is not None
                if reference == "catalog_inactive":
                    catalog.is_active = False
                else:
                    catalog.deleted_at = datetime.now(UTC)

        # Keep strong references and prove the reader still has the pre-commit state.
        assert cached_branch.is_active and cached_supplier.is_active and cached_catalog.is_active
        assert cached_catalog.deleted_at is None
        expected_error = BusinessRuleError if reference == "branch" else ConflictError
        message = "Branch is inactive" if reference == "branch" else "unavailable"
        with pytest.raises(expected_error, match=message):
            await IncomingService(IncomingRepository(reader)).accept(context.document_ids[0])
        await _assert_effects(reader, context, accepted_count=0)
        doc = await reader.get(IncomingDocument, context.document_ids[0])
        assert doc is not None and doc.status == "draft"
