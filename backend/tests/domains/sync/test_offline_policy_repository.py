"""An immutable SQL inbox survives commits but never activates offline authority."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.sync.offline_auth import OfflineAuthUnavailableError
from app.domains.sync.offline_policy_repository import OfflinePolicyInboxRepository
from app.domains.sync.offline_policy_service import OfflinePolicyInboxService
from app.domains.sync.repository import SyncCloudRepository
from app.domains.sync.schemas import OfflinePolicyDeliveryScopeV1, SignedOfflinePolicyDeliveryV1
from tests.domains.sync.offline_policy_fixtures import NOW, SigningFixture, binding, claims
from tests.domains.sync.test_writer_handover import _insert_readiness, _prepare_handover


@dataclass(frozen=True)
class _InboxState:
    scope: OfflinePolicyDeliveryScopeV1
    signer: SigningFixture

    def delivery(self, sequence: int = 1, version: int = 7) -> SignedOfflinePolicyDeliveryV1:
        value = claims(sequence=sequence, version=version)
        return self.signer.sign_claims(
            value.model_copy(
                update={
                    "delivery_id": uuid4(),
                    "scope": self.scope,
                    "policy": value.policy.model_copy(update={"tenant_id": self.scope.tenant_id}),
                }
            )
        )


@pytest_asyncio.fixture
async def inbox_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        get_settings().DATABASE_URL_APP,
        poolclass=NullPool,
        connect_args={"server_settings": {"lock_timeout": "5s", "statement_timeout": "30s"}},
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def inbox_state(db_engine: AsyncEngine) -> _InboxState:
    # Provision through existing Cloud handover commands. Tests never provision
    # enrollment through the inbox. Immutable history stays in this disposable DB.
    async with AsyncSession(db_engine, expire_on_commit=False) as db, db.begin():
        await db.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        foundation = FoundationService(FoundationRepository(db))
        tenant = await foundation.create_tenant(
            payload={
                "name": "Policy inbox fixture",
                "contact_email": f"inbox-{uuid4().hex}@example.invalid",
                "status": "active",
            }
        )
        branch = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "Inbox"})
        register = await foundation.create_register(
            tenant_id=tenant.id, fields={"branch_id": branch.id, "name": "Inbox"}
        )
        _admin, node, _stream, prepared, _request = await _prepare_handover(
            db, {"tenant": tenant, "branch": branch, "register": register}
        )
        await _insert_readiness(db, node=node, epoch=prepared)
        await SyncCloudRepository(db).activate_writer_handover(
            activation_id=prepared.activation_id,
            tenant_id=tenant.id,
            branch_id=branch.id,
            activation_manifest_hash=prepared.activation_manifest_hash,
        )
        scope = OfflinePolicyDeliveryScopeV1(
            activation_id=prepared.activation_id,
            tenant_id=tenant.id,
            branch_id=branch.id,
            edge_node_id=node.id,
            register_id=register.id,
            writer_epoch=prepared.writer_epoch,
            capability="cash_sale_v1",
        )
    return _InboxState(scope, SigningFixture())


async def _context(db: AsyncSession, scope: OfflinePolicyDeliveryScopeV1) -> None:
    await db.execute(
        text(
            "SELECT set_config('app.tenant_id', :tenant, true), "
            "set_config('app.branch_id', :branch, true), "
            "set_config('app.edge_node_id', :node, true)"
        ),
        {
            "tenant": str(scope.tenant_id),
            "branch": str(scope.branch_id),
            "node": str(scope.edge_node_id),
        },
    )


def _service(engine: AsyncEngine, state: _InboxState) -> OfflinePolicyInboxService:
    return OfflinePolicyInboxService(
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        expected_scope=state.scope,
        expected_device=binding(),
        signature_verifier=state.signer.verifier,
    )


async def test_inbox_persists_envelope_and_trigger_audit_without_activation(
    inbox_engine: AsyncEngine, inbox_state: _InboxState, db_engine: AsyncEngine
) -> None:
    first, second = inbox_state.delivery(), inbox_state.delivery(2, 8)
    async with AsyncSession(inbox_engine) as db, db.begin():
        await _context(db, inbox_state.scope)
        repo = OfflinePolicyInboxRepository(db)
        await repo.lock_target(inbox_state.scope)
        await repo.assert_enrollment(inbox_state.scope)
        await repo.insert(first)
        await repo.insert(second)
    async with AsyncSession(inbox_engine) as db, db.begin():
        await _context(db, inbox_state.scope)
        repo = OfflinePolicyInboxRepository(db)
        row = await repo.get_by_id(inbox_state.scope, first.claims.delivery_id)
        assert row is not None and row.payload == first.model_dump(mode="json")
        assert row.status == "pending_protected_acceptance"
        assert row.created_at is not None and row.created_by is None
        latest = await repo.get_latest(inbox_state.scope)
        assert latest is not None and latest.id == second.claims.delivery_id
        assert latest.tenant_access_revision == 8
        assert await db.scalar(text("SELECT count(*) FROM local_auth_session")) == 0
    async with db_engine.connect() as db:
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM audit_log WHERE table_name='offline_policy_inbox' "
                    "AND tenant_id=:tenant"
                ),
                {"tenant": inbox_state.scope.tenant_id},
            )
            == 2
        )


async def test_inbox_rollback_does_not_persist_a_delivery(
    inbox_engine: AsyncEngine, inbox_state: _InboxState
) -> None:
    async with AsyncSession(inbox_engine) as db:
        await _context(db, inbox_state.scope)
        await OfflinePolicyInboxRepository(db).insert(inbox_state.delivery())
        await db.rollback()
    async with AsyncSession(inbox_engine) as db, db.begin():
        await _context(db, inbox_state.scope)
        assert await OfflinePolicyInboxRepository(db).get_latest(inbox_state.scope) is None


async def test_inbox_rls_hides_and_rejects_other_tenant_data(
    inbox_engine: AsyncEngine, inbox_state: _InboxState
) -> None:
    await _service(inbox_engine, inbox_state).stage(inbox_state.delivery())
    async with AsyncSession(inbox_engine) as db, db.begin():
        await _context(db, inbox_state.scope.model_copy(update={"tenant_id": uuid4()}))
        repo = OfflinePolicyInboxRepository(db)
        assert await repo.get_latest(inbox_state.scope) is None
        with pytest.raises(ValueError, match="enrollment"):
            await repo.assert_enrollment(inbox_state.scope)
        with pytest.raises(DBAPIError) as denied:
            async with db.begin_nested():
                await repo.insert(inbox_state.delivery(2))
        assert getattr(denied.value.orig, "sqlstate", None) == "42501"


async def test_inbox_has_no_mutation_path_even_for_owner(
    inbox_engine: AsyncEngine, inbox_state: _InboxState, maintenance_engine: AsyncEngine
) -> None:
    envelope = inbox_state.delivery()
    await _service(inbox_engine, inbox_state).stage(envelope)
    for engine in (inbox_engine, maintenance_engine):
        for query in (
            "UPDATE offline_policy_inbox SET status='pending_protected_acceptance' WHERE id=:id",
            "DELETE FROM offline_policy_inbox WHERE id=:id",
        ):
            with pytest.raises(DBAPIError) as denied:
                async with AsyncSession(engine) as db, db.begin():
                    await _context(db, inbox_state.scope)
                    await db.execute(text(query), {"id": envelope.claims.delivery_id})
            assert getattr(denied.value.orig, "sqlstate", None) == "42501"


async def test_inbox_fk_and_sequence_constraints_are_authoritative(
    inbox_engine: AsyncEngine, inbox_state: _InboxState
) -> None:
    async with AsyncSession(inbox_engine) as db, db.begin():
        await _context(db, inbox_state.scope)
        repo = OfflinePolicyInboxRepository(db)
        await repo.insert(inbox_state.delivery())
        with pytest.raises(IntegrityError) as duplicate:
            async with db.begin_nested():
                await repo.insert(inbox_state.delivery())
        assert getattr(duplicate.value.orig, "sqlstate", None) == "23505"
        bad_scope = inbox_state.scope.model_copy(update={"activation_id": uuid4()})
        unknown = _InboxState(bad_scope, inbox_state.signer).delivery(2)
        with pytest.raises(IntegrityError) as missing:
            async with db.begin_nested():
                await repo.insert(unknown)
        assert getattr(missing.value.orig, "sqlstate", None) == "23503"


async def test_staged_policy_remains_inactive_after_writer_fencing(
    inbox_engine: AsyncEngine, inbox_state: _InboxState, maintenance_engine: AsyncEngine
) -> None:
    # The read is deliberately not a lifecycle lock: later fencing is safe
    # because staging never serves as current authorization or a sealed floor.
    async with AsyncSession(inbox_engine) as db, db.begin():
        await _context(db, inbox_state.scope)
        repo = OfflinePolicyInboxRepository(db)
        await repo.lock_target(inbox_state.scope)
        await repo.assert_enrollment(inbox_state.scope)
        async with maintenance_engine.begin() as owner:
            await owner.execute(
                text(
                    "UPDATE sync_writer_epoch SET state='fenced', fenced_at=now() "
                    "WHERE activation_id=:activation"
                ),
                {"activation": inbox_state.scope.activation_id},
            )
        row = await repo.insert(inbox_state.delivery())
        assert row.status == "pending_protected_acceptance"
        with pytest.raises(ValueError, match="enrollment"):
            await repo.assert_enrollment(inbox_state.scope)
    with pytest.raises(OfflineAuthUnavailableError):
        await _service(inbox_engine, inbox_state).read_latest()


async def test_service_serializes_duplicate_deliveries_and_rejects_rollback(
    inbox_engine: AsyncEngine, inbox_state: _InboxState
) -> None:
    service = _service(inbox_engine, inbox_state)
    envelope = inbox_state.delivery(2, 8)
    receipts = await asyncio.wait_for(
        asyncio.gather(service.stage(envelope), service.stage(envelope)), timeout=10
    )
    assert sorted(receipt.duplicate for receipt in receipts) == [False, True]
    assert await service.read_latest() == envelope
    later = inbox_state.delivery(3, 9)
    stale_time = inbox_state.signer.sign_claims(
        later.claims.model_copy(update={"issued_at": NOW - timedelta(seconds=1)})
    )
    changed_id_payload = inbox_state.signer.sign_claims(
        envelope.claims.model_copy(
            update={"policy": envelope.claims.policy.model_copy(update={"mode": "readonly"})}
        )
    )
    for stale in (
        inbox_state.delivery(1, 8),
        inbox_state.delivery(3, 7),
        inbox_state.delivery(2, 9),
        stale_time,
        changed_id_payload,
    ):
        with pytest.raises(OfflineAuthUnavailableError):
            await service.stage(stale)
    assert await service.read_latest() == envelope


async def test_policy_only_archive_delivery_stages_without_granting_a_new_lease(
    inbox_engine: AsyncEngine, inbox_state: _InboxState
) -> None:
    service = _service(inbox_engine, inbox_state)
    await service.stage(inbox_state.delivery())
    later = inbox_state.delivery(2)
    archived = inbox_state.signer.sign_claims(
        later.claims.model_copy(
            update={"policy": later.claims.policy.model_copy(update={"tenant_status": "archived"})}
        )
    )
    assert archived.claims.lease is None
    receipt = await service.stage(archived)
    assert not receipt.duplicate
    assert receipt.tenant_access_revision == 7
    assert await service.read_latest() == archived
    async with AsyncSession(inbox_engine) as db, db.begin():
        await _context(db, inbox_state.scope)
        row = await OfflinePolicyInboxRepository(db).get_latest(inbox_state.scope)
        assert row is not None and row.status == "pending_protected_acceptance"
        assert await db.scalar(text("SELECT count(*) FROM local_auth_session")) == 0
