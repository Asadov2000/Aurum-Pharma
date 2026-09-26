"""Versioned offline lease contract; runtime admission remains fail-closed.

The digest is a consistency check, not signature verification. Neither a valid
digest nor successful schema validation authorizes an offline session. A future
positive verifier must enforce hardware identity, trusted time, durable revocation
and anti-rollback, active reference revisions and per-shift reauthentication.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Never

from app.domains.sync.integrity import canonical_json_bytes
from app.domains.sync.offline_auth import (
    OFFLINE_AUTH_UNAVAILABLE_MESSAGE,
    OfflineAuthContractError,
    OfflineAuthUnavailableError,
)
from app.domains.sync.schemas import (
    OfflineAuthLeaseClaimsV1,
    OfflinePosCommand,
    SignedOfflineAuthLeaseV1,
)

OFFLINE_AUTH_LEASE_DOMAIN = b"aurum:offline-auth-lease:v1\x00"


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
