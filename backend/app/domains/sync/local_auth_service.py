"""Durable local-login algorithm. Deliberately disconnected from HTTP/runtime.

The service owns its SQL transaction: invalid-secret counters commit before the
generic denial is raised. SQL durability alone does not prove anti-rollback;
every attempt additionally requires the independent protected-state adapter.
"""

from __future__ import annotations

import asyncio
import hmac
from collections.abc import Callable
from datetime import timedelta
from typing import NoReturn
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.sync.local_auth_repository import LocalAuthRepository
from app.domains.sync.local_auth_state import (
    LocalAuthGuardSnapshotV1,
    TrustedLocalLoginStateProviderV1,
    TrustedLocalLoginStateTransactionV1,
)
from app.domains.sync.local_credentials import (
    LocalCredentialContextV1,
    local_credential_context_digest,
    verify_local_credential,
)
from app.domains.sync.models import LocalAuthGuard
from app.domains.sync.offline_auth import (
    OFFLINE_AUTH_UNAVAILABLE_MESSAGE,
    DeviceBindingVerifier,
    OfflineAuthUnavailableError,
    TrustedClockReading,
)
from app.domains.sync.offline_auth_lease import (
    OfflineLeaseCloudSignatureVerifierV1,
    assert_offline_auth_lease_claims_hash,
    offline_auth_lease_claims_bytes,
)
from app.domains.sync.offline_auth_lease_state import (
    LocalOfflineLeaseSessionV1,
    OfflineLeaseAuthorizationStateV1,
)
from app.domains.sync.schemas import OfflineAuthLeaseClaimsV1, SignedOfflineAuthLeaseV1

_MAX_FAILURES = 5
_LOCK_DURATION = timedelta(minutes=5)
_MAX_SEQUENCE = 9_223_372_036_854_775_807


def _deny() -> NoReturn:
    raise OfflineAuthUnavailableError(OFFLINE_AUTH_UNAVAILABLE_MESSAGE)


def _snapshot(guard: LocalAuthGuard) -> LocalAuthGuardSnapshotV1:
    return LocalAuthGuardSnapshotV1(
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


def _assert_admission(
    authorization: OfflineLeaseAuthorizationStateV1,
    claims: OfflineAuthLeaseClaimsV1,
    context: LocalCredentialContextV1,
) -> None:
    if (
        authorization.status != "active"
        or authorization.lease_id != claims.lease_id
        or authorization.scope != claims.scope
        or authorization.device_binding != claims.device_binding
        or authorization.policy_revision != claims.policy_revision
        or authorization.subject_revision != claims.subject_revision
        or authorization.admission != claims.admission
        or authorization.local_credential != claims.local_credential
        or authorization.credential_audience != claims.credential_audience
        or authorization.reauthentication_policy.reference != claims.reauthentication_policy
        or not set(claims.allowed_commands).issubset(authorization.allowed_commands)
        or context.scope != claims.scope
        or context.local_credential != claims.local_credential
        or context.device_binding != claims.device_binding
        or context.audience != claims.credential_audience
    ):
        _deny()


def _assert_time(guard: LocalAuthGuard, clock: TrustedClockReading) -> None:
    if (
        clock.continuity_id != guard.continuity_id
        or clock.utc_now < guard.last_utc
        or clock.monotonic_counter < guard.last_counter
        or clock.monotonic_counter > _MAX_SEQUENCE
        or len(clock.continuity_id) > 128
    ):
        _deny()


class LocalOfflineAuthenticatorV1:
    """No Cloud password, refresh token or caller-provided session is accepted."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AsyncSession],
        signature_verifier: OfflineLeaseCloudSignatureVerifierV1,
        device_binding_verifier: DeviceBindingVerifier,
        state_provider: TrustedLocalLoginStateProviderV1,
    ) -> None:
        self._session_factory = session_factory
        self._signature = signature_verifier
        self._device = device_binding_verifier
        self._state = state_provider

    async def _attempt(
        self,
        db: AsyncSession,
        trusted: TrustedLocalLoginStateTransactionV1,
        claims: OfflineAuthLeaseClaimsV1,
        secret: str,
    ) -> LocalOfflineLeaseSessionV1 | None:
        result: LocalOfflineLeaseSessionV1 | None = None
        # Identity is taken only from the verified lease, before any tenant query.
        await db.execute(
            text(
                "SELECT set_config('app.tenant_id', :tenant, true), "
                "set_config('app.user_id', :actor, true)"
            ),
            {
                "tenant": str(claims.scope.tenant_id),
                "actor": str(claims.scope.user_id),
            },
        )
        repo = LocalAuthRepository(db)
        guard = await repo.lock_guard(
            tenant_id=claims.scope.tenant_id,
            edge_node_id=claims.scope.edge_node_id,
            user_id=claims.scope.user_id,
            credential_id=claims.local_credential.reference_id,
        )
        if guard is None:
            _deny()
        # No auto-enrollment, revision reset, or upsert is permitted here.
        assert guard is not None
        authorization_raw = await trusted.read_authorization()
        if authorization_raw is None:
            _deny()
        assert authorization_raw is not None
        authorization = OfflineLeaseAuthorizationStateV1.model_validate(
            authorization_raw.model_dump(mode="python")
        )
        material = await trusted.read_credential()
        context = LocalCredentialContextV1.model_validate(
            material.context.model_dump(mode="python")
        )
        _assert_admission(authorization, claims, context)
        if (
            guard.branch_id != claims.scope.branch_id
            or guard.credential_revision != claims.local_credential.revision
            or not hmac.compare_digest(
                guard.context_digest, local_credential_context_digest(context).hex()
            )
            or guard.attempt_sequence >= _MAX_SEQUENCE
        ):
            _deny()
        await trusted.assert_current_guard(_snapshot(guard))
        clock = await trusted.read_trusted_time()
        _assert_time(guard, clock)
        if not claims.issued_at <= clock.utc_now < claims.expires_at or (
            guard.blocked_until is not None and clock.utc_now < guard.blocked_until
        ):
            _deny()
        await self._device.assert_possession(claims.device_binding)
        await repo.lock_workplace(
            tenant_id=claims.scope.tenant_id,
            branch_id=claims.scope.branch_id,
            edge_node_id=claims.scope.edge_node_id,
            register_id=claims.scope.register_id,
        )
        # bcrypt runs off the event loop while SQL/protected-state locks stay held.
        valid_secret = await asyncio.to_thread(
            verify_local_credential,
            material.record,
            secret,
            expected_context=context,
            pepper=material.pepper,
        )
        after = await trusted.read_trusted_time()
        _assert_time(guard, after)
        if after.utc_now < clock.utc_now or after.monotonic_counter < clock.monotonic_counter:
            _deny()
        # Re-read authorization before persisting either outcome.
        if await trusted.read_authorization() != authorization:
            _deny()
        guard.attempt_sequence += 1
        guard.last_utc = after.utc_now
        guard.last_counter = after.monotonic_counter
        if valid_secret:
            if not claims.issued_at <= after.utc_now < claims.expires_at:
                _deny()
            guard.failed_attempts = 0
            guard.blocked_until = None
            result = LocalOfflineLeaseSessionV1(
                session_id=uuid4(),
                lease_id=claims.lease_id,
                scope=claims.scope,
                local_credential=claims.local_credential,
                credential_audience=claims.credential_audience,
                reauthentication_policy=claims.reauthentication_policy,
                shift_context_id=authorization.shift_context_id,
                authenticated_at=after.utc_now,
                expires_at=min(
                    claims.expires_at,
                    after.utc_now
                    + timedelta(seconds=authorization.reauthentication_policy.max_session_seconds),
                ),
                continuity_id=after.continuity_id,
                monotonic_counter=after.monotonic_counter,
            )
            await repo.replace_session(result)
        else:
            guard.failed_attempts = min(_MAX_FAILURES, guard.failed_attempts + 1)
            if guard.failed_attempts >= _MAX_FAILURES:
                guard.blocked_until = after.utc_now + _LOCK_DURATION
        await db.flush()
        await trusted.seal_next_guard(_snapshot(guard), replacement=result)
        return result

    async def authenticate(
        self, lease: SignedOfflineAuthLeaseV1, secret: str
    ) -> LocalOfflineLeaseSessionV1:
        result: LocalOfflineLeaseSessionV1 | None = None
        transaction_prepared = False
        sql_committed = False
        state_completed = False
        try:
            validated = SignedOfflineAuthLeaseV1.model_validate(lease.model_dump(mode="python"))
            assert_offline_auth_lease_claims_hash(validated)
            await self._signature.verify(
                validated, offline_auth_lease_claims_bytes(validated.claims)
            )
            claims = validated.claims
            async with self._state.serialized() as trusted:
                async with self._session_factory() as db:
                    async with db.begin():
                        result = await self._attempt(db, trusted, claims, secret)
                        transaction_prepared = True
                    # Assign only after begin exits, so a suppressed exception
                    # cannot authorize an incomplete attempt.
                    sql_committed = True
                state_completed = True
            if (
                not transaction_prepared
                or not sql_committed
                or not state_completed
                or result is None
            ):
                _deny()
            return result
        except Exception:
            raise OfflineAuthUnavailableError(OFFLINE_AUTH_UNAVAILABLE_MESSAGE) from None
