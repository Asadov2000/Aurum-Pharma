"""Fail-closed contracts for future Edge offline authentication.

This module deliberately has no positive runtime composition. Hardware-backed
TPM identity, trusted time, local-session continuity, authorization snapshots,
and atomic anti-rollback storage must all exist before an offline grant can
authorize a POS command.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.domains.sync.integrity import canonical_json_bytes
from app.domains.sync.schemas import (
    OfflineAuthDeviceBindingV0,
    OfflineAuthGrantClaimsV0,
    OfflineAuthScopeV0,
    OfflinePosCommand,
    SignedOfflineAuthGrantV0,
)

OFFLINE_AUTH_GRANT_DOMAIN = b"aurum:offline-auth-grant:v0\x00"


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


@dataclass(frozen=True, slots=True)
class OfflineAuthRevisionObservation:
    grant_id: UUID
    user_id: UUID
    policy_revision: int
    subject_revision: int
    trusted_time: TrustedClockReading


@dataclass(frozen=True, slots=True)
class VerifiedOfflinePrincipalV0:
    grant_id: UUID
    user_id: UUID
    command: OfflinePosCommand
    expires_at: datetime


class CloudSignatureVerifier(Protocol):
    async def verify(self, grant: SignedOfflineAuthGrantV0, signing_bytes: bytes) -> None: ...


class ActiveScopeProvider(Protocol):
    async def current(self) -> OfflineAuthScopeV0 | None: ...


class TrustedClock(Protocol):
    async def read(self) -> TrustedClockReading: ...


class DeviceBindingVerifier(Protocol):
    async def assert_possession(self, binding: OfflineAuthDeviceBindingV0) -> None: ...


class LocalSessionProvider(Protocol):
    async def current(self) -> LocalOfflineSession | None: ...


class AuthorizationSnapshot(Protocol):
    async def commands(
        self,
        scope: OfflineAuthScopeV0,
        *,
        policy_revision: int,
        subject_revision: int,
    ) -> frozenset[OfflinePosCommand] | None: ...


class RevisionRollbackGuard(Protocol):
    async def check_and_advance(self, observation: OfflineAuthRevisionObservation) -> None: ...


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
class DenyAllOfflineAuthVerifier:
    """Only verifier allowed in runtime composition until hardware conformance."""

    async def authorize(
        self,
        grant: SignedOfflineAuthGrantV0,
        command: OfflinePosCommand,
    ) -> VerifiedOfflinePrincipalV0:
        del grant, command
        raise OfflineAuthUnavailableError("Offline authentication is unavailable")


def runtime_offline_auth_verifier() -> OfflineAuthVerifier:
    """Return the non-configurable runtime policy for substrate v0."""
    return DenyAllOfflineAuthVerifier()
