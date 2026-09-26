"""Verify inert Cloud policy deliveries with explicit, separately pinned keys."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from uuid import UUID

from app.domains.sync.integrity import canonical_json_bytes
from app.domains.sync.offline_auth import (
    OFFLINE_AUTH_UNAVAILABLE_MESSAGE,
    OfflineAuthUnavailableError,
)
from app.domains.sync.offline_auth_lease import offline_auth_lease_claims_bytes
from app.domains.sync.offline_lease_signature import (
    Ed25519OfflineLeaseSignatureVerifier,
    _verify_ed25519,
    trusted_offline_signing_keys,
)
from app.domains.sync.offline_tenant_access import permitted_offline_commands
from app.domains.sync.schemas import (
    OfflinePolicyDeliveryClaimsV1,
    SignedOfflinePolicyDeliveryV1,
)

_DOMAIN = b"aurum-edge-policy-delivery-v1\x00"


def offline_policy_delivery_bytes(claims: OfflinePolicyDeliveryClaimsV1) -> bytes:
    validated = OfflinePolicyDeliveryClaimsV1.model_validate(
        claims.model_dump(mode="python", warnings="error")
    )
    return _DOMAIN + canonical_json_bytes(validated.model_dump(mode="json"))


def offline_policy_delivery_hash(claims: OfflinePolicyDeliveryClaimsV1) -> str:
    return hashlib.sha256(offline_policy_delivery_bytes(claims)).hexdigest()


class Ed25519OfflinePolicyDeliveryVerifier:
    """A verified package remains pending; this never produces an auth principal.

    Policy and optional lease keyrings are provisioned independently of the input.
    Embedded keys, key downloads and implicit key discovery are not supported.
    """

    def __init__(
        self,
        *,
        policy_keys: Mapping[UUID, bytes],
        lease_keys: Mapping[UUID, bytes],
    ) -> None:
        self._policy_keys = trusted_offline_signing_keys(policy_keys)
        self._lease_verifier = Ed25519OfflineLeaseSignatureVerifier(lease_keys)

    async def verify(self, delivery: SignedOfflinePolicyDeliveryV1) -> None:
        try:
            validated = SignedOfflinePolicyDeliveryV1.model_validate(
                delivery.model_dump(mode="python", warnings="error")
            )
            claims = validated.claims
            signing_bytes = offline_policy_delivery_bytes(claims)
            if not hmac.compare_digest(validated.claims_hash, offline_policy_delivery_hash(claims)):
                raise ValueError("Policy delivery hash mismatch")
            _verify_ed25519(
                self._policy_keys[validated.signing_key_id],
                signing_bytes,
                bytes.fromhex(validated.signature),
            )
            if claims.lease is not None:
                lease = claims.lease
                await self._lease_verifier.verify(
                    lease, offline_auth_lease_claims_bytes(lease.claims)
                )
                allowed = permitted_offline_commands(
                    claims.policy,
                    tenant_id=claims.scope.tenant_id,
                    commands=lease.claims.allowed_commands,
                    now=claims.issued_at,
                )
                if allowed != lease.claims.allowed_commands:
                    raise ValueError("Delivered lease exceeds the delivered policy")
                if lease.claims.expires_at <= claims.issued_at:
                    raise ValueError("Delivered lease has expired")
        except Exception:
            raise OfflineAuthUnavailableError(OFFLINE_AUTH_UNAVAILABLE_MESSAGE) from None
