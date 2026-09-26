"""Local auth state survives independent transactions and enforces tenant boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domains.sync import local_auth_service
from app.domains.sync.local_auth_repository import LocalAuthRepository
from app.domains.sync.local_auth_state import (
    LocalAuthGuardSnapshotV1,
    TrustedLocalCredentialMaterialV1,
)
from app.domains.sync.local_credentials import (
    LocalCredentialContextV1,
    LocalCredentialVerifierV1,
    local_credential_context_digest,
)
from app.domains.sync.models import LocalAuthGuard, LocalAuthSession
from app.domains.sync.offline_auth import OfflineAuthUnavailableError
from app.domains.sync.offline_auth_lease_state import (
    LocalOfflineLeaseSessionV1,
    OfflineLeaseAuthorizationStateV1,
    OfflineLeaseReauthenticationPolicyV1,
)
from app.domains.sync.schemas import (
    OfflineAuthDeviceBindingV0,
    OfflineAuthLeaseReferenceV1,
    OfflineAuthScopeV0,
)
from tests.domains.sync.test_local_auth_service import NOW, PEPPER, _claims, _Harness, _signed


@dataclass(frozen=True)
class _LocalState:
    scope: OfflineAuthScopeV0
    credential_id: UUID


@pytest_asyncio.fixture
async def local_app_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def local_state(
    db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> AsyncIterator[_LocalState]:
    tenant_id, user_id = uuid4(), uuid4()
    state = _LocalState(
        OfflineAuthScopeV0(
            activation_id=uuid4(),
            tenant_id=tenant_id,
            branch_id=uuid4(),
            edge_node_id=uuid4(),
            register_id=uuid4(),
            writer_epoch=1,
            user_id=user_id,
            capability="cash_sale_v1",
        ),
        uuid4(),
    )
    async with db_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenant (id, name, contact_email) VALUES (:id, :name, :email)"),
            {
                "id": tenant_id,
                "name": f"Local auth {tenant_id}",
                "email": f"local-{tenant_id}@example.invalid",
            },
        )
        await connection.execute(
            text(
                "INSERT INTO app_user (id, email, full_name, home_tenant_id, status) "
                "VALUES (:id, :email, 'Local test actor', :tenant_id, 'active')"
            ),
            {"id": user_id, "email": f"local-{user_id}@example.invalid", "tenant_id": tenant_id},
        )
        async with AsyncSession(bind=connection) as session:
            session.add(_guard(state))
            await session.flush()
    try:
        yield state
    finally:
        # Owner deletion is confined to explicitly created rows in the disposable test DB.
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            for table in ("local_auth_session", "local_auth_guard"):
                await connection.execute(
                    text(f"DELETE FROM public.{table} WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
        async with db_engine.begin() as connection:
            await connection.execute(text("DELETE FROM tenant WHERE id=:id"), {"id": tenant_id})
            # Audit keeps an immutable FK to this synthetic actor. Keep that
            # actor until the disposable test database itself is retired.


def _guard(state: _LocalState) -> LocalAuthGuard:
    return LocalAuthGuard(
        tenant_id=state.scope.tenant_id,
        branch_id=state.scope.branch_id,
        edge_node_id=state.scope.edge_node_id,
        user_id=state.scope.user_id,
        credential_id=state.credential_id,
        credential_revision=1,
        context_digest="a" * 64,
        failed_attempts=0,
        attempt_sequence=0,
        last_utc=datetime.now(UTC),
        last_counter=0,
        continuity_id="boot-test",
    )


def _record(state: _LocalState) -> LocalOfflineLeaseSessionV1:
    now = datetime.now(UTC)
    return LocalOfflineLeaseSessionV1(
        session_id=uuid4(),
        lease_id=uuid4(),
        scope=state.scope,
        local_credential=OfflineAuthLeaseReferenceV1(reference_id=state.credential_id, revision=1),
        credential_audience="aurum-edge-local-auth-v1",
        reauthentication_policy=OfflineAuthLeaseReferenceV1(reference_id=uuid4(), revision=1),
        shift_context_id=uuid4(),
        authenticated_at=now,
        expires_at=now + timedelta(hours=1),
        continuity_id="boot-test",
        monotonic_counter=1,
    )


async def _context(session: AsyncSession, state: _LocalState) -> None:
    await session.execute(
        text(
            "SELECT set_config('app.tenant_id', :tenant_id, true), "
            "set_config('app.user_id', :user_id, true)"
        ),
        {"tenant_id": str(state.scope.tenant_id), "user_id": str(state.scope.user_id)},
    )


async def _lock_guard(repository: LocalAuthRepository, state: _LocalState) -> LocalAuthGuard:
    guard = await repository.lock_guard(
        tenant_id=state.scope.tenant_id,
        edge_node_id=state.scope.edge_node_id,
        user_id=state.scope.user_id,
        credential_id=state.credential_id,
    )
    assert guard is not None
    return guard


async def _lock_workplace(repository: LocalAuthRepository, state: _LocalState) -> None:
    await repository.lock_workplace(
        tenant_id=state.scope.tenant_id,
        branch_id=state.scope.branch_id,
        edge_node_id=state.scope.edge_node_id,
        register_id=state.scope.register_id,
    )


async def _wait_until_locked(engine: AsyncEngine, pid: int) -> None:
    async def poll() -> None:
        async with engine.connect() as connection:
            while not await connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=:pid AND NOT granted)"),
                {"pid": pid},
            ):
                await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout=5)


async def test_guard_is_not_created_by_login_lookup(
    local_app_engine: AsyncEngine, local_state: _LocalState
) -> None:
    async with AsyncSession(local_app_engine) as session:
        await _context(session, local_state)
        repository = LocalAuthRepository(session)
        assert (
            await repository.lock_guard(
                tenant_id=local_state.scope.tenant_id,
                edge_node_id=local_state.scope.edge_node_id,
                user_id=local_state.scope.user_id,
                credential_id=uuid4(),
            )
            is None
        )
        assert await session.scalar(select(func.count()).select_from(LocalAuthGuard)) == 1


async def test_guard_serializes_failed_attempts_and_survives_new_connection(
    local_app_engine: AsyncEngine, local_state: _LocalState
) -> None:
    pid_ready: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def second_attempt() -> None:
        async with AsyncSession(local_app_engine) as session, session.begin():
            await _context(session, local_state)
            pid_ready.set_result(await session.scalar(text("SELECT pg_backend_pid()")))
            guard = await _lock_guard(LocalAuthRepository(session), local_state)
            guard.failed_attempts += 1
            guard.attempt_sequence += 1

    async with AsyncSession(local_app_engine) as first, first.begin():
        await _context(first, local_state)
        guard = await _lock_guard(LocalAuthRepository(first), local_state)
        guard.failed_attempts = 1
        guard.attempt_sequence = 1
        guard.blocked_until = datetime.now(UTC) + timedelta(minutes=1)
        contender = asyncio.create_task(second_attempt())
        try:
            await _wait_until_locked(local_app_engine, await pid_ready)
            assert not contender.done()
        except BaseException:
            contender.cancel()
            await asyncio.gather(contender, return_exceptions=True)
            raise
    await asyncio.wait_for(contender, timeout=5)
    async with AsyncSession(local_app_engine) as reopened:
        await _context(reopened, local_state)
        persisted = await _lock_guard(LocalAuthRepository(reopened), local_state)
        assert persisted.failed_attempts == persisted.attempt_sequence == 2
        assert persisted.blocked_until is not None
        assert persisted.updated_by == local_state.scope.user_id


async def test_workplace_serializes_replacement_and_preserves_revoked_history(
    local_app_engine: AsyncEngine, local_state: _LocalState
) -> None:
    first_record, second_record = _record(local_state), _record(local_state)
    pid_ready: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def replace() -> None:
        async with AsyncSession(local_app_engine) as session, session.begin():
            await _context(session, local_state)
            pid_ready.set_result(await session.scalar(text("SELECT pg_backend_pid()")))
            repository = LocalAuthRepository(session)
            await _lock_workplace(repository, local_state)
            assert await repository.read_current_session(local_state.scope) == first_record
            await repository.replace_session(second_record)

    async with AsyncSession(local_app_engine) as first, first.begin():
        await _context(first, local_state)
        repository = LocalAuthRepository(first)
        await _lock_workplace(repository, local_state)
        await repository.replace_session(first_record)
        contender = asyncio.create_task(replace())
        try:
            await _wait_until_locked(local_app_engine, await pid_ready)
            assert not contender.done()
        except BaseException:
            contender.cancel()
            await asyncio.gather(contender, return_exceptions=True)
            raise
    await asyncio.wait_for(contender, timeout=5)
    async with AsyncSession(local_app_engine) as reopened:
        await _context(reopened, local_state)
        assert (
            await LocalAuthRepository(reopened).read_current_session(local_state.scope)
            == second_record
        )
        history = (await reopened.scalars(select(LocalAuthSession))).all()
        assert len(history) == 2
        assert sum(row.revoked_at is None for row in history) == 1
        assert next(row for row in history if row.id == first_record.session_id).revoked_at == (
            second_record.authenticated_at
        )
        assert all(row.created_by == local_state.scope.user_id for row in history)


async def test_rollback_keeps_previous_current_session(
    local_app_engine: AsyncEngine, local_state: _LocalState
) -> None:
    record = _record(local_state)
    async with AsyncSession(local_app_engine) as session, session.begin():
        await _context(session, local_state)
        await LocalAuthRepository(session).replace_session(record)
    async with AsyncSession(local_app_engine) as session:
        await _context(session, local_state)
        await LocalAuthRepository(session).replace_session(_record(local_state))
        await session.rollback()
    async with AsyncSession(local_app_engine) as session:
        await _context(session, local_state)
        assert await LocalAuthRepository(session).read_current_session(local_state.scope) == record


async def test_app_role_tenant_isolation_and_forced_rls(
    local_app_engine: AsyncEngine, local_state: _LocalState
) -> None:
    async with AsyncSession(local_app_engine) as session, session.begin():
        await _context(session, local_state)
        await LocalAuthRepository(session).replace_session(_record(local_state))
    async with AsyncSession(local_app_engine) as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(uuid4())},
        )
        assert await session.scalar(select(func.count()).select_from(LocalAuthGuard)) == 0
        assert await session.scalar(select(func.count()).select_from(LocalAuthSession)) == 0
        assert await LocalAuthRepository(session).read_current_session(local_state.scope) is None
        with pytest.raises(DBAPIError) as denied:
            await LocalAuthRepository(session).replace_session(_record(local_state))
        assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    async with local_app_engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid IN ('public.local_auth_guard'::regclass, "
                    "'public.local_auth_session'::regclass)"
                )
            )
        ).all()
        assert len(rows) == 2 and all(
            row.relrowsecurity and row.relforcerowsecurity for row in rows
        )


@pytest.mark.parametrize("role", ["aurum_app", "aurum_support"])
async def test_runtime_roles_cannot_delete_local_auth_state(
    local_app_engine: AsyncEngine, role: str
) -> None:
    async with local_app_engine.connect() as connection:
        for table in ("local_auth_guard", "local_auth_session"):
            assert not await connection.scalar(
                text("SELECT has_table_privilege(:role, :table, 'DELETE')"),
                {"role": role, "table": f"public.{table}"},
            )


@pytest.mark.parametrize(
    "field,value",
    [
        ("failed_attempts", -1),
        ("attempt_sequence", -1),
        ("last_counter", -1),
        ("credential_revision", 0),
        ("context_digest", "A" * 64),
        ("continuity_id", ""),
    ],
)
async def test_database_rejects_invalid_guard_state(
    local_app_engine: AsyncEngine, local_state: _LocalState, field: str, value: str | int
) -> None:
    async with AsyncSession(local_app_engine) as session:
        await _context(session, local_state)
        guard = await _lock_guard(LocalAuthRepository(session), local_state)
        setattr(guard, field, value)
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_current_workplace_uniqueness_and_trigger_audit(
    local_app_engine: AsyncEngine, db_engine: AsyncEngine, local_state: _LocalState
) -> None:
    record = _record(local_state)
    async with AsyncSession(local_app_engine) as session, session.begin():
        await _context(session, local_state)
        await LocalAuthRepository(session).replace_session(record)
    async with AsyncSession(local_app_engine) as session:
        await _context(session, local_state)
        session.add(
            LocalAuthSession(
                id=uuid4(),
                tenant_id=local_state.scope.tenant_id,
                branch_id=local_state.scope.branch_id,
                edge_node_id=local_state.scope.edge_node_id,
                register_id=local_state.scope.register_id,
                user_id=uuid4(),
                credential_id=uuid4(),
                payload=record.model_dump(mode="json"),
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
    async with db_engine.connect() as connection:
        events = (
            await connection.execute(
                text(
                    "SELECT table_name, action FROM audit_log WHERE tenant_id=:tenant_id "
                    "AND table_name IN ('local_auth_guard', 'local_auth_session')"
                ),
                {"tenant_id": local_state.scope.tenant_id},
            )
        ).all()
        assert ("local_auth_guard", "INSERT") in events
        assert ("local_auth_session", "INSERT") in events


async def _service_state(engine: AsyncEngine, state: _LocalState, *, failures: int = 0) -> _Harness:
    """Reuse synthetic adapters; SQL sessions and repository remain real and independent."""
    claims = _claims().model_copy(
        update={
            "scope": state.scope,
            "local_credential": OfflineAuthLeaseReferenceV1(
                reference_id=state.credential_id, revision=1
            ),
        }
    )
    context = LocalCredentialContextV1(
        schema_version=1,
        audience=claims.credential_audience,
        scope=claims.scope,
        local_credential=claims.local_credential,
        device_binding=claims.device_binding,
        protection_key=OfflineAuthDeviceBindingV0(
            method="tpm2",
            key_id=uuid4(),
            spki_sha256="ef" * 32,
        ),
    )
    # Format-only verifier; the explicit spy below replaces bcrypt, never TPM or crypto claims.
    material = TrustedLocalCredentialMaterialV1(
        context,
        LocalCredentialVerifierV1(
            schema_version=1,
            algorithm="bcrypt-hmac-sha384-v1",
            context=context,
            verifier=SecretStr("$2b$12$" + "a" * 53),
        ),
        PEPPER,
    )
    async with AsyncSession(engine) as session, session.begin():
        await _context(session, state)
        guard = await _lock_guard(LocalAuthRepository(session), state)
        guard.context_digest = local_credential_context_digest(context).hex()
        guard.last_utc = NOW - timedelta(seconds=1)
        guard.last_counter = 99
        guard.continuity_id = "boot-one"
        guard.failed_attempts = failures
        guard.attempt_sequence = 10
        snapshot = LocalAuthGuardSnapshotV1(
            tenant_id=guard.tenant_id,
            branch_id=guard.branch_id,
            edge_node_id=guard.edge_node_id,
            user_id=guard.user_id,
            credential_id=guard.credential_id,
            credential_revision=guard.credential_revision,
            context_digest=guard.context_digest,
            failed_attempts=guard.failed_attempts,
            attempt_sequence=guard.attempt_sequence,
            blocked_until=guard.blocked_until,
            last_utc=guard.last_utc,
            last_counter=guard.last_counter,
            continuity_id=guard.continuity_id,
        )
    authorization = OfflineLeaseAuthorizationStateV1(
        lease_id=claims.lease_id,
        scope=claims.scope,
        device_binding=claims.device_binding,
        policy_revision=claims.policy_revision,
        tenant_access_revision=claims.tenant_access_revision,
        subject_revision=claims.subject_revision,
        allowed_commands=claims.allowed_commands,
        admission=claims.admission,
        local_credential=claims.local_credential,
        credential_audience=claims.credential_audience,
        reauthentication_policy=OfflineLeaseReauthenticationPolicyV1(
            reference=claims.reauthentication_policy,
            max_session_seconds=600,
            confirmation_commands=("shift.open",),
            max_confirmation_seconds=30,
        ),
        shift_context_id=uuid4(),
        status="active",
    )
    return _Harness(material, snapshot, authorization, _signed(claims), snapshot)


def _authenticator(
    engine: AsyncEngine, state: _Harness
) -> local_auth_service.LocalOfflineAuthenticatorV1:
    return local_auth_service.LocalOfflineAuthenticatorV1(
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        signature_verifier=state,
        device_binding_verifier=state,
        state_provider=state,
    )


async def test_real_service_commits_bad_secret_then_replaces_successful_session(
    local_app_engine: AsyncEngine, local_state: _LocalState, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = await _service_state(local_app_engine, local_state)
    verifier = Mock(side_effect=[False, True, True])
    monkeypatch.setattr(local_auth_service, "verify_local_credential", verifier)
    with pytest.raises(OfflineAuthUnavailableError):
        await _authenticator(local_app_engine, state).authenticate(
            state.lease, "synthetic-bad-secret"
        )
    async with AsyncSession(local_app_engine) as reopened:
        await _context(reopened, local_state)
        guard = await _lock_guard(LocalAuthRepository(reopened), local_state)
        assert guard.failed_attempts == 1 and guard.attempt_sequence == 11
        assert await LocalAuthRepository(reopened).read_current_session(local_state.scope) is None
    first = await _authenticator(local_app_engine, state).authenticate(
        state.lease, "synthetic-good"
    )
    second = await _authenticator(local_app_engine, state).authenticate(
        state.lease, "synthetic-good"
    )
    async with AsyncSession(local_app_engine) as reopened:
        await _context(reopened, local_state)
        guard = await _lock_guard(LocalAuthRepository(reopened), local_state)
        assert guard.failed_attempts == 0 and guard.attempt_sequence == 13
        assert guard.blocked_until is None
        assert await LocalAuthRepository(reopened).read_current_session(local_state.scope) == second
        history = (await reopened.scalars(select(LocalAuthSession))).all()
        assert len(history) == 2
        assert next(row for row in history if row.id == first.session_id).revoked_at is not None
        assert second.session_id != first.session_id
    assert verifier.call_count == 3


async def test_real_service_concurrent_fifth_failure_performs_one_secret_check(
    local_app_engine: AsyncEngine, local_state: _LocalState, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = await _service_state(local_app_engine, local_state, failures=4)
    verifier = Mock(return_value=False)
    monkeypatch.setattr(local_auth_service, "verify_local_credential", verifier)
    outcomes = await asyncio.gather(
        _authenticator(local_app_engine, state).authenticate(state.lease, "synthetic-bad-secret"),
        _authenticator(local_app_engine, state).authenticate(state.lease, "synthetic-bad-secret"),
        return_exceptions=True,
    )
    assert all(isinstance(result, OfflineAuthUnavailableError) for result in outcomes)
    assert verifier.call_count == 1
    async with AsyncSession(local_app_engine) as reopened:
        await _context(reopened, local_state)
        guard = await _lock_guard(LocalAuthRepository(reopened), local_state)
        assert guard.failed_attempts == 5 and guard.attempt_sequence == 11
        assert guard.blocked_until == NOW + timedelta(seconds=1, minutes=5)
        assert await LocalAuthRepository(reopened).read_current_session(local_state.scope) is None
