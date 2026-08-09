"""Fail-closed contracts and decision pipeline for Edge offline authentication.

The pure decision pipeline can only authorize when every security dependency
succeeds. Runtime deliberately has no positive composition: hardware-backed TPM
identity, trusted time, local-session continuity, authorization snapshots, and
atomic anti-rollback storage must all exist before offline auth can be enabled.
"""

from __future__ import annotations

import hashlib
import hmac
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from pydantic import ConfigDict, TypeAdapter

from app.domains.sync.integrity import canonical_json_bytes
from app.domains.sync.schemas import (
    OfflineAuthDeviceBindingV0,
    OfflineAuthGrantClaimsV0,
    OfflineAuthScopeV0,
    OfflinePosCommand,
    SignedOfflineAuthGrantV0,
)

OFFLINE_AUTH_GRANT_DOMAIN = b"aurum:offline-auth-grant:v0\x00"
OFFLINE_AUTH_UNAVAILABLE_MESSAGE = "Offline authentication is unavailable"
_OFFLINE_POS_COMMAND_ADAPTER: TypeAdapter[OfflinePosCommand] = TypeAdapter(
    OfflinePosCommand,
    config=ConfigDict(strict=True),
)


class OfflineAuthContractError(ValueError):
    """An offline grant is malformed or does not match its canonical digest."""


class OfflineAuthUnavailableError(RuntimeError):
    """The production offline-auth security dependencies are unavailable."""


@dataclass(frozen=True, slots=True)
class TrustedClockReading:
    utc_now: datetime
    monotonic_counter: int
    continuity_id: str

    def __post_init__(self) -> None:
        if self.utc_now.tzinfo is None or self.utc_now.utcoffset() != timedelta(0):
            raise ValueError("trusted clock must return UTC")
        if self.monotonic_counter < 0:
            raise ValueError("trusted clock counter must be non-negative")
        if not self.continuity_id:
            raise ValueError("trusted clock continuity_id is required")


@dataclass(frozen=True, slots=True)
class LocalOfflineSession:
    user_id: UUID
    authenticated_online_at: datetime

    def __post_init__(self) -> None:
        if (
            self.authenticated_online_at.tzinfo is None
            or self.authenticated_online_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("local session authentication time must be UTC")


@dataclass(frozen=True, slots=True)
class OfflineAuthorizationSnapshotV0:
    scope: OfflineAuthScopeV0
    policy_revision: int
    subject_revision: int
    commands: frozenset[OfflinePosCommand]

    def __post_init__(self) -> None:
        OfflineAuthScopeV0.model_validate(self.scope)
        if self.policy_revision < 1 or self.subject_revision < 1:
            raise ValueError("authorization revisions must be positive")
        if not isinstance(self.commands, frozenset):
            raise ValueError("authorization commands must be a frozenset")
        for command in self.commands:
            _OFFLINE_POS_COMMAND_ADAPTER.validate_python(command)


@dataclass(frozen=True, slots=True)
class OfflineAuthRevisionObservation:
    grant_id: UUID
    user_id: UUID
    policy_revision: int
    subject_revision: int
    trusted_time: TrustedClockReading

    def __post_init__(self) -> None:
        if self.policy_revision < 1 or self.subject_revision < 1:
            raise ValueError("observed authorization revisions must be positive")


@dataclass(frozen=True, slots=True)
class VerifiedOfflinePrincipalV0:
    grant_id: UUID
    user_id: UUID
    command: OfflinePosCommand
    expires_at: datetime
    scope: OfflineAuthScopeV0


class CloudSignatureVerifier(Protocol):
    async def verify(self, grant: SignedOfflineAuthGrantV0, signing_bytes: bytes) -> None: ...


class DeviceBindingVerifier(Protocol):
    async def assert_possession(self, binding: OfflineAuthDeviceBindingV0) -> None: ...


class OfflineAuthStateTransaction(Protocol):
    """Serialized view of every mutable input to an offline-auth decision."""

    async def read_active_scope(self) -> OfflineAuthScopeV0 | None: ...

    async def read_active_device_binding(
        self,
    ) -> OfflineAuthDeviceBindingV0 | None: ...

    async def read_local_session(self) -> LocalOfflineSession | None: ...

    async def read_authorization_snapshot(
        self, scope: OfflineAuthScopeV0
    ) -> OfflineAuthorizationSnapshotV0 | None: ...

    async def read_trusted_time(self) -> TrustedClockReading: ...

    async def check_and_advance(self, observation: OfflineAuthRevisionObservation) -> None: ...


class OfflineAuthStateTransactionProvider(Protocol):
    """Open a lock-held, crash-safe transaction spanning all mutable checks."""

    def serialized(
        self,
    ) -> AbstractAsyncContextManager[OfflineAuthStateTransaction]: ...


class OfflineAuthVerifier(Protocol):
    async def authorize(
        self,
        grant: SignedOfflineAuthGrantV0,
        command: OfflinePosCommand,
    ) -> VerifiedOfflinePrincipalV0: ...


def offline_auth_claims_bytes(claims: OfflineAuthGrantClaimsV0) -> bytes:
    """Canonical, domain-separated bytes signed by the future Cloud issuer."""
    return OFFLINE_AUTH_GRANT_DOMAIN + canonical_json_bytes(claims.model_dump(mode="json"))


def offline_auth_claims_hash(claims: OfflineAuthGrantClaimsV0) -> str:
    return hashlib.sha256(offline_auth_claims_bytes(claims)).hexdigest()


def assert_offline_auth_claims_hash(grant: SignedOfflineAuthGrantV0) -> None:
    expected = offline_auth_claims_hash(grant.claims)
    if not hmac.compare_digest(expected, grant.claims_hash):
        raise OfflineAuthContractError("Offline-auth claims hash does not match")


@dataclass(frozen=True, slots=True)
class OfflineAuthDecisionPipeline:
    """Evaluate every invariant atomically and collapse all failures to deny."""

    signature_verifier: CloudSignatureVerifier
    device_binding_verifier: DeviceBindingVerifier
    state_transaction_provider: OfflineAuthStateTransactionProvider

    async def authorize(
        self,
        grant: SignedOfflineAuthGrantV0,
        command: OfflinePosCommand,
    ) -> VerifiedOfflinePrincipalV0:
        try:
            return await self._authorize(grant, command)
        except Exception:
            raise OfflineAuthUnavailableError(OFFLINE_AUTH_UNAVAILABLE_MESSAGE) from None

    async def _authorize(
        self,
        grant: SignedOfflineAuthGrantV0,
        command: OfflinePosCommand,
    ) -> VerifiedOfflinePrincipalV0:
        validated_grant = SignedOfflineAuthGrantV0.model_validate(grant.model_dump(mode="python"))
        validated_command = _OFFLINE_POS_COMMAND_ADAPTER.validate_python(command)
        assert_offline_auth_claims_hash(validated_grant)
        claims = validated_grant.claims

        await self.signature_verifier.verify(
            validated_grant,
            offline_auth_claims_bytes(claims),
        )
        if validated_command not in claims.allowed_commands:
            raise OfflineAuthContractError("Command is absent from the signed grant")

        async with self.state_transaction_provider.serialized() as state:
            active_scope = await state.read_active_scope()
            if active_scope is None or active_scope != claims.scope:
                raise OfflineAuthContractError("Offline-auth scope is not active")

            active_device_binding = await state.read_active_device_binding()
            if active_device_binding is None or active_device_binding != claims.device_binding:
                raise OfflineAuthContractError("Offline-auth device binding is not active")

            await self.device_binding_verifier.assert_possession(claims.device_binding)

            local_session = await state.read_local_session()
            if (
                local_session is None
                or local_session.user_id != claims.scope.user_id
                or local_session.authenticated_online_at != claims.authenticated_at
            ):
                raise OfflineAuthContractError("Local session does not match the grant")

            snapshot = await state.read_authorization_snapshot(claims.scope)
            if (
                snapshot is None
                or snapshot.scope != claims.scope
                or snapshot.policy_revision != claims.policy_revision
                or snapshot.subject_revision != claims.subject_revision
                or validated_command not in snapshot.commands
            ):
                raise OfflineAuthContractError("Authorization snapshot does not allow command")

            trusted_time = await state.read_trusted_time()
            if not claims.issued_at <= trusted_time.utc_now < claims.expires_at:
                raise OfflineAuthContractError("Offline-auth grant is not currently valid")

            await state.check_and_advance(
                OfflineAuthRevisionObservation(
                    grant_id=claims.grant_id,
                    user_id=claims.scope.user_id,
                    policy_revision=claims.policy_revision,
                    subject_revision=claims.subject_revision,
                    trusted_time=trusted_time,
                )
            )

        return VerifiedOfflinePrincipalV0(
            grant_id=claims.grant_id,
            user_id=claims.scope.user_id,
            command=validated_command,
            expires_at=claims.expires_at,
            scope=claims.scope,
        )


@dataclass(frozen=True, slots=True)
class DenyAllOfflineAuthVerifier:
    """Only verifier allowed in runtime composition until hardware conformance."""

    async def authorize(
        self,
        grant: SignedOfflineAuthGrantV0,
        command: OfflinePosCommand,
    ) -> VerifiedOfflinePrincipalV0:
        del grant, command
        raise OfflineAuthUnavailableError(OFFLINE_AUTH_UNAVAILABLE_MESSAGE)


def runtime_offline_auth_verifier() -> OfflineAuthVerifier:
    """Return the non-configurable runtime policy for substrate v0."""
    return DenyAllOfflineAuthVerifier()
