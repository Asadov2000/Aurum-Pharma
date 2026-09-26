"""Versioned offline lease contract; runtime admission remains fail-closed.

The digest is a consistency check, not signature verification. Neither a valid
digest nor successful schema validation authorizes an offline session. The
decision pipeline requires explicitly injected trusted adapters. Runtime has no
positive composition until hardware identity, trusted time and durable state
have passed acceptance on the target Windows configuration.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Never, Protocol
from uuid import UUID

from pydantic import ConfigDict, TypeAdapter

from app.domains.sync.integrity import canonical_json_bytes
from app.domains.sync.offline_auth import (
    OFFLINE_AUTH_UNAVAILABLE_MESSAGE,
    DeviceBindingVerifier,
    OfflineAuthContractError,
    OfflineAuthUnavailableError,
    TrustedClockReading,
)
from app.domains.sync.offline_auth_lease_state import (
    LocalOfflineLeaseSessionV1,
    OfflineLeaseAuthorizationStateV1,
    OfflineLeaseRevisionObservationV1,
    OfflineLeaseStateTransactionProviderV1,
)
from app.domains.sync.schemas import (
    OfflineAuthLeaseClaimsV1,
    OfflineAuthScopeV0,
    OfflinePosCommand,
    SignedOfflineAuthLeaseV1,
)

OFFLINE_AUTH_LEASE_DOMAIN = b"aurum:offline-auth-lease:v1\x00"
_COMMAND_ADAPTER: TypeAdapter[OfflinePosCommand] = TypeAdapter(
    OfflinePosCommand, config=ConfigDict(strict=True)
)


def offline_auth_lease_claims_bytes(claims: OfflineAuthLeaseClaimsV1) -> bytes:
    """Canonical bytes under a domain distinct from the 72-hour v0 grant."""
    validated = OfflineAuthLeaseClaimsV1.model_validate(claims.model_dump(mode="python"))
    return OFFLINE_AUTH_LEASE_DOMAIN + canonical_json_bytes(validated.model_dump(mode="json"))


def offline_auth_lease_claims_hash(claims: OfflineAuthLeaseClaimsV1) -> str:
    return hashlib.sha256(offline_auth_lease_claims_bytes(claims)).hexdigest()


def assert_offline_auth_lease_claims_hash(lease: SignedOfflineAuthLeaseV1) -> None:
    validated = SignedOfflineAuthLeaseV1.model_validate(lease.model_dump(mode="python"))
    expected = offline_auth_lease_claims_hash(validated.claims)
    if not hmac.compare_digest(expected, validated.claims_hash):
        raise OfflineAuthContractError("Offline lease claims hash does not match")


class OfflineLeaseCloudSignatureVerifierV1(Protocol):
    """Verify Ed25519 with a trusted Cloud key, never with a lease-supplied key."""

    async def verify(self, lease: SignedOfflineAuthLeaseV1, signing_bytes: bytes) -> None: ...


@dataclass(frozen=True, slots=True)
class VerifiedOfflineLeasePrincipalV1:
    lease_id: UUID
    session_id: UUID
    scope: OfflineAuthScopeV0
    command: OfflinePosCommand
    expires_at: datetime


def _assert_active_authorization(
    state: OfflineLeaseAuthorizationStateV1,
    claims: OfflineAuthLeaseClaimsV1,
    command: OfflinePosCommand,
) -> None:
    if (
        state.status != "active"
        or state.lease_id != claims.lease_id
        or state.scope != claims.scope
        or state.device_binding != claims.device_binding
        or state.policy_revision != claims.policy_revision
        or state.tenant_access_revision != claims.tenant_access_revision
        or state.subject_revision != claims.subject_revision
        or state.admission != claims.admission
        or state.local_credential != claims.local_credential
        or state.credential_audience != claims.credential_audience
        or state.reauthentication_policy.reference != claims.reauthentication_policy
        or command not in state.allowed_commands
    ):
        raise OfflineAuthContractError("Local authorization does not match the lease")


def _session_deadline(
    session: LocalOfflineLeaseSessionV1,
    authorization: OfflineLeaseAuthorizationStateV1,
    claims: OfflineAuthLeaseClaimsV1,
    clock: TrustedClockReading,
    command: OfflinePosCommand,
) -> datetime:
    policy = authorization.reauthentication_policy
    if (
        session.lease_id != claims.lease_id
        or session.scope != claims.scope
        or session.local_credential != claims.local_credential
        or session.credential_audience != claims.credential_audience
        or session.reauthentication_policy != claims.reauthentication_policy
        or session.shift_context_id != authorization.shift_context_id
        or session.continuity_id != clock.continuity_id
        or session.monotonic_counter > clock.monotonic_counter
        or not claims.issued_at <= session.authenticated_at <= clock.utc_now
        or session.expires_at > claims.expires_at
        or session.expires_at
        > session.authenticated_at + timedelta(seconds=policy.max_session_seconds)
        or clock.utc_now >= session.expires_at
    ):
        raise OfflineAuthContractError("Local session is not current for this lease and shift")
    deadline = min(session.expires_at, claims.expires_at)
    if command in policy.confirmation_commands:
        confirmation = session.confirmation
        if (
            confirmation is None
            or confirmation.command != command
            or not session.authenticated_at <= confirmation.confirmed_at <= clock.utc_now
            or not session.monotonic_counter
            <= confirmation.monotonic_counter
            <= clock.monotonic_counter
        ):
            raise OfflineAuthContractError("Command requires a fresh local confirmation")
        deadline = min(
            deadline,
            confirmation.confirmed_at + timedelta(seconds=policy.max_confirmation_seconds),
        )
        if clock.utc_now >= deadline:
            raise OfflineAuthContractError("Local command confirmation has expired")
    return deadline


@dataclass(frozen=True, slots=True)
class OfflineAuthLeaseDecisionPipelineV1:
    """Pure adapter-driven algorithm; does not create sessions or enable runtime login."""

    signature_verifier: OfflineLeaseCloudSignatureVerifierV1
    device_binding_verifier: DeviceBindingVerifier
    state_transaction_provider: OfflineLeaseStateTransactionProviderV1

    async def authorize(
        self, lease: SignedOfflineAuthLeaseV1, command: OfflinePosCommand
    ) -> VerifiedOfflineLeasePrincipalV1:
        try:
            return await self._authorize(lease, command)
        except Exception:
            raise OfflineAuthUnavailableError(OFFLINE_AUTH_UNAVAILABLE_MESSAGE) from None

    async def _authorize(
        self, lease: SignedOfflineAuthLeaseV1, command: OfflinePosCommand
    ) -> VerifiedOfflineLeasePrincipalV1:
        validated = SignedOfflineAuthLeaseV1.model_validate(lease.model_dump(mode="python"))
        command = _COMMAND_ADAPTER.validate_python(command)
        assert_offline_auth_lease_claims_hash(validated)
        claims = validated.claims
        await self.signature_verifier.verify(validated, offline_auth_lease_claims_bytes(claims))
        if command not in claims.allowed_commands:
            raise OfflineAuthContractError("Command is absent from the signed lease")

        principal: VerifiedOfflineLeasePrincipalV1 | None = None
        async with self.state_transaction_provider.serialized() as transaction:
            raw_authorization = await transaction.read_authorization()
            if raw_authorization is None:
                raise OfflineAuthContractError("Local authorization is unavailable")
            authorization = OfflineLeaseAuthorizationStateV1.model_validate(
                raw_authorization.model_dump(mode="python")
            )
            await transaction.assert_current_authorization(authorization)
            _assert_active_authorization(authorization, claims, command)
            await self.device_binding_verifier.assert_possession(claims.device_binding)

            raw_session = await transaction.read_local_session()
            if raw_session is None:
                raise OfflineAuthContractError("Local authentication is required")
            session = LocalOfflineLeaseSessionV1.model_validate(
                raw_session.model_dump(mode="python")
            )
            raw_clock = await transaction.read_trusted_time()
            clock = TrustedClockReading(
                utc_now=raw_clock.utc_now,
                monotonic_counter=raw_clock.monotonic_counter,
                continuity_id=raw_clock.continuity_id,
            )
            if (
                type(clock.monotonic_counter) is not int
                or clock.monotonic_counter > 9_223_372_036_854_775_807
                or not isinstance(clock.continuity_id, str)
                or not claims.issued_at <= clock.utc_now < claims.expires_at
            ):
                raise OfflineAuthContractError("Trusted time does not allow the lease")
            expires_at = _session_deadline(session, authorization, claims, clock, command)
            await transaction.check_and_advance(
                OfflineLeaseRevisionObservationV1(
                    authorization=authorization, session=session, trusted_time=clock
                )
            )
            principal = VerifiedOfflineLeasePrincipalV1(
                lease_id=claims.lease_id,
                session_id=session.session_id,
                scope=claims.scope,
                command=command,
                expires_at=expires_at,
            )

        # A failed __aexit__/durable commit must never leak an authorized principal.
        if principal is None:
            raise OfflineAuthContractError("Local authorization did not complete")
        return principal


@dataclass(frozen=True, slots=True)
class DenyAllOfflineLeaseVerifierV1:
    """No positive runtime composition before hardware and recovery acceptance."""

    async def authorize(
        self,
        lease: SignedOfflineAuthLeaseV1,
        command: OfflinePosCommand,
    ) -> Never:
        del lease, command
        raise OfflineAuthUnavailableError(OFFLINE_AUTH_UNAVAILABLE_MESSAGE)


def runtime_offline_auth_lease_verifier() -> DenyAllOfflineLeaseVerifierV1:
    """No environment switch can enable thirty-day offline authentication."""
    return DenyAllOfflineLeaseVerifierV1()
