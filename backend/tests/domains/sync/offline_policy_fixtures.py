"""Shared real signatures for policy verification and disposable SQL inbox tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from Crypto.PublicKey import ECC
from Crypto.PublicKey.ECC import EccKey
from Crypto.Signature import eddsa

from app.domains.sync.offline_auth_lease import (
    offline_auth_lease_claims_bytes,
    offline_auth_lease_claims_hash,
)
from app.domains.sync.offline_policy_signature import (
    Ed25519OfflinePolicyDeliveryVerifier,
    offline_policy_delivery_bytes,
    offline_policy_delivery_hash,
)
from app.domains.sync.schemas import (
    CloudTenantAccessPolicyV1,
    OfflineAuthDeviceBindingV0,
    OfflineAuthLeaseClaimsV1,
    OfflineAuthLeaseReferenceV1,
    OfflineAuthScopeV0,
    OfflinePolicyDeliveryClaimsV1,
    OfflinePolicyDeliveryScopeV1,
    SignedOfflineAuthLeaseV1,
    SignedOfflinePolicyDeliveryV1,
)
from app.domains.tenant_access.schemas import AccessModule

NOW = datetime(2026, 9, 26, 9, tzinfo=UTC)
TenantStatus = Literal[
    "setup", "trial", "active", "grace_period", "readonly", "suspended", "archived"
]
PolicyMode = Literal["full", "readonly"]


def uuid(value: int) -> UUID:
    return UUID(f"{value:08x}-0000-4000-8000-000000000001")


def scope() -> OfflinePolicyDeliveryScopeV1:
    return OfflinePolicyDeliveryScopeV1(
        activation_id=uuid(1),
        tenant_id=uuid(2),
        branch_id=uuid(3),
        edge_node_id=uuid(4),
        register_id=uuid(5),
        writer_epoch=6,
        capability="cash_sale_v1",
    )


def binding() -> OfflineAuthDeviceBindingV0:
    return OfflineAuthDeviceBindingV0(method="tpm2", key_id=uuid(8), spki_sha256="ab" * 32)


def policy(
    *,
    version: int = 7,
    status: TenantStatus = "active",
    mode: PolicyMode = "full",
    blocked_modules: tuple[AccessModule, ...] = (),
    expires_at: datetime | None = None,
) -> CloudTenantAccessPolicyV1:
    return CloudTenantAccessPolicyV1(
        tenant_id=uuid(2),
        version=version,
        tenant_status=status,
        mode=mode,
        blocked_modules=blocked_modules,
        expires_at=expires_at,
    )


def lease_claims(*, version: int = 7) -> OfflineAuthLeaseClaimsV1:
    return OfflineAuthLeaseClaimsV1(
        schema_version=1,
        lease_version=1,
        lease_id=uuid(20),
        issuer="aurum-cloud",
        audience="aurum-edge-offline-auth-v1",
        auth_context="owner-approved-local-credential",
        scope=OfflineAuthScopeV0(**scope().model_dump(mode="python"), user_id=uuid(9)),
        device_binding=binding(),
        allowed_commands=("receipt.print", "sale.cash.complete"),
        policy_revision=11,
        tenant_access_revision=version,
        subject_revision=13,
        admission=OfflineAuthLeaseReferenceV1(reference_id=uuid(14), revision=1),
        local_credential=OfflineAuthLeaseReferenceV1(reference_id=uuid(15), revision=2),
        reauthentication_policy=OfflineAuthLeaseReferenceV1(reference_id=uuid(16), revision=3),
        credential_audience="aurum-edge-local-auth-v1",
        issued_at=NOW,
        expires_at=NOW + timedelta(days=30),
    )


def claims(
    *,
    sequence: int = 1,
    version: int = 7,
    status: TenantStatus = "active",
    mode: PolicyMode = "full",
    lease: SignedOfflineAuthLeaseV1 | None = None,
) -> OfflinePolicyDeliveryClaimsV1:
    return OfflinePolicyDeliveryClaimsV1(
        schema_version=1,
        issuer="aurum-cloud",
        audience="aurum-edge-policy-delivery-v1",
        delivery_id=uuid(100 + sequence),
        delivery_sequence=sequence,
        scope=scope(),
        device_binding=binding(),
        policy=policy(version=version, status=status, mode=mode),
        issued_at=NOW,
        lease=lease,
    )


def _private_key() -> EccKey:
    # Test keys are random, kept in memory and excluded from fixture repr.
    return ECC.generate(curve="Ed25519")


@dataclass(frozen=True)
class SigningFixture:
    policy_private_key: EccKey = field(default_factory=_private_key, repr=False)
    lease_private_key: EccKey = field(default_factory=_private_key, repr=False)

    @property
    def policy_keys(self) -> dict[UUID, bytes]:
        return {uuid(17): self.policy_private_key.public_key().export_key(format="raw")}

    @property
    def lease_keys(self) -> dict[UUID, bytes]:
        return {uuid(18): self.lease_private_key.public_key().export_key(format="raw")}

    @property
    def verifier(self) -> Ed25519OfflinePolicyDeliveryVerifier:
        return Ed25519OfflinePolicyDeliveryVerifier(
            policy_keys=self.policy_keys, lease_keys=self.lease_keys
        )

    def policy_signature(self, message: bytes) -> str:
        return eddsa.new(self.policy_private_key, "rfc8032").sign(message).hex()

    def sign_claims(self, value: OfflinePolicyDeliveryClaimsV1) -> SignedOfflinePolicyDeliveryV1:
        return SignedOfflinePolicyDeliveryV1(
            claims=value,
            claims_hash=offline_policy_delivery_hash(value),
            signing_key_id=uuid(17),
            signature_algorithm="ed25519-v1",
            signature=self.policy_signature(offline_policy_delivery_bytes(value)),
        )

    def sign_lease(self, value: OfflineAuthLeaseClaimsV1) -> SignedOfflineAuthLeaseV1:
        signature = (
            eddsa.new(self.lease_private_key, "rfc8032")
            .sign(offline_auth_lease_claims_bytes(value))
            .hex()
        )
        return SignedOfflineAuthLeaseV1(
            claims=value,
            claims_hash=offline_auth_lease_claims_hash(value),
            signing_key_id=uuid(18),
            signature_algorithm="ed25519-v1",
            signature=signature,
        )

    def signed_delivery(
        self,
        *,
        sequence: int = 1,
        version: int = 7,
        status: TenantStatus = "active",
        mode: PolicyMode = "full",
        with_lease: bool = False,
    ) -> SignedOfflinePolicyDeliveryV1:
        lease = self.sign_lease(lease_claims(version=version)) if with_lease else None
        return self.sign_claims(
            claims(sequence=sequence, version=version, status=status, mode=mode, lease=lease)
        )
