"""Activation-bound foundation bootstrap for development Edge nodes.

The persisted snapshot is server-owned and immutable. Its HMAC transport is
development-only; production activation remains blocked until mTLS and an
asymmetric device-signing stack are available.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.domains.sync.credentials import parse_edge_credential
from app.domains.sync.integrity import canonical_json_bytes, canonical_json_hash
from app.domains.sync.schemas import (
    SyncActivationBootstrapManifest,
    SyncActivationBootstrapRead,
    SyncActivationFoundationSnapshot,
)

SIGNATURE_ALGORITHM = "hmac-sha256-edge-activation-v1"
PROFILE = "foundation_shadow_v1"
_KEY_DOMAIN = b"aurum-edge-activation-bootstrap-mac-v1\x00"


class ActivationBootstrapValidationError(ValueError):
    """Activation bootstrap is expired, corrupted, or outside its scope."""


@dataclass(frozen=True, slots=True)
class ActivationSnapshotScope:
    activation_id: UUID
    tenant_id: UUID
    branch_id: UUID
    edge_node_id: UUID
    register_id: UUID
    writer_epoch: int
    previous_writer_epoch: int
    previous_terminal_sequence: int
    previous_terminal_source_checksum: str
    previous_terminal_projection_checksum: str
    receipt_baseline_seq: int


def _utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None:
        raise ActivationBootstrapValidationError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def foundation_hash(snapshot: SyncActivationFoundationSnapshot) -> str:
    return canonical_json_hash(snapshot.model_dump(mode="json", by_alias=True))


def snapshot_hash(*, scope: ActivationSnapshotScope, foundation_digest: str) -> str:
    seed = ":".join(
        (
            "aurum:activation-foundation-snapshot:v1",
            "1",
            PROFILE,
            "false",
            str(scope.activation_id),
            str(scope.tenant_id),
            str(scope.branch_id),
            str(scope.edge_node_id),
            str(scope.register_id),
            "cash_sale_v1",
            str(scope.writer_epoch),
            str(scope.previous_writer_epoch),
            str(scope.previous_terminal_sequence),
            scope.previous_terminal_source_checksum,
            scope.previous_terminal_projection_checksum,
            str(scope.receipt_baseline_seq),
            foundation_digest,
        )
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def manifest_hash(manifest: SyncActivationBootstrapManifest) -> str:
    return canonical_json_hash(manifest.model_dump(mode="json"))


def _mac_key(credential_digest: str) -> bytes:
    if len(credential_digest) != 64:
        raise ActivationBootstrapValidationError("Credential digest is invalid")
    try:
        digest_bytes = bytes.fromhex(credential_digest)
    except ValueError as exc:
        raise ActivationBootstrapValidationError("Credential digest is invalid") from exc
    return hmac.new(digest_bytes, _KEY_DOMAIN, hashlib.sha256).digest()


def _sign(
    manifest: SyncActivationBootstrapManifest,
    *,
    credential_digest: str,
) -> str:
    payload = canonical_json_bytes(manifest.model_dump(mode="json"))
    return hmac.new(_mac_key(credential_digest), payload, hashlib.sha256).hexdigest()


def build_activation_bootstrap(
    *,
    scope: ActivationSnapshotScope,
    foundation: SyncActivationFoundationSnapshot,
    stored_foundation_hash: str,
    stored_snapshot_hash: str,
    activation_manifest_hash: str,
    credential_kid: UUID,
    credential_digest: str,
    prepared_at: datetime,
    credential_expires_at: datetime,
    ttl_seconds: int,
    now: datetime,
) -> SyncActivationBootstrapRead:
    calculated_foundation_hash = foundation_hash(foundation)
    if not hmac.compare_digest(calculated_foundation_hash, stored_foundation_hash):
        raise ActivationBootstrapValidationError("Foundation snapshot hash does not match")
    calculated_snapshot_hash = snapshot_hash(
        scope=scope,
        foundation_digest=calculated_foundation_hash,
    )
    if not hmac.compare_digest(calculated_snapshot_hash, stored_snapshot_hash):
        raise ActivationBootstrapValidationError("Activation snapshot hash does not match")

    issued_at = _utc(prepared_at, field="prepared_at")
    expires_at = min(
        _utc(credential_expires_at, field="credential_expires_at"),
        issued_at + timedelta(seconds=ttl_seconds),
    )
    now = _utc(now, field="now")
    if expires_at <= issued_at or now >= expires_at:
        raise ActivationBootstrapValidationError("Activation bootstrap has expired")

    manifest = SyncActivationBootstrapManifest(
        activation_id=scope.activation_id,
        tenant_id=scope.tenant_id,
        branch_id=scope.branch_id,
        edge_node_id=scope.edge_node_id,
        register_id=scope.register_id,
        writer_epoch=scope.writer_epoch,
        previous_writer_epoch=scope.previous_writer_epoch,
        previous_terminal_sequence=scope.previous_terminal_sequence,
        previous_terminal_source_checksum=scope.previous_terminal_source_checksum,
        previous_terminal_projection_checksum=(scope.previous_terminal_projection_checksum),
        receipt_baseline_seq=scope.receipt_baseline_seq,
        foundation_hash=calculated_foundation_hash,
        snapshot_hash=calculated_snapshot_hash,
        activation_manifest_hash=activation_manifest_hash,
        credential_kid=credential_kid,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    return SyncActivationBootstrapRead(
        manifest=manifest,
        foundation=foundation,
        manifest_hash=manifest_hash(manifest),
        signature_algorithm=SIGNATURE_ALGORITHM,
        signature=_sign(manifest, credential_digest=credential_digest),
    )


def verify_activation_bootstrap(
    signed: SyncActivationBootstrapRead,
    *,
    credential: str,
    now: datetime,
) -> SyncActivationBootstrapManifest:
    parsed = parse_edge_credential(credential)
    manifest = signed.manifest
    if signed.signature_algorithm != SIGNATURE_ALGORITHM:
        raise ActivationBootstrapValidationError(
            "Unsupported activation bootstrap signature algorithm"
        )
    if parsed.kid != manifest.credential_kid:
        raise ActivationBootstrapValidationError("Activation credential scope does not match")
    expected_manifest_hash = manifest_hash(manifest)
    if not hmac.compare_digest(expected_manifest_hash, signed.manifest_hash):
        raise ActivationBootstrapValidationError("Activation manifest hash does not match")
    if not hmac.compare_digest(
        _sign(manifest, credential_digest=parsed.digest),
        signed.signature,
    ):
        raise ActivationBootstrapValidationError("Activation signature does not match")

    now = _utc(now, field="now")
    issued_at = _utc(manifest.issued_at, field="issued_at")
    expires_at = _utc(manifest.expires_at, field="expires_at")
    if expires_at <= issued_at or now >= expires_at:
        raise ActivationBootstrapValidationError("Activation bootstrap has expired")
    if issued_at > now + timedelta(minutes=5):
        raise ActivationBootstrapValidationError("Activation bootstrap was issued in the future")

    foundation = signed.foundation
    if (
        foundation.tenant.id != manifest.tenant_id
        or foundation.settings.tenant_id != manifest.tenant_id
        or foundation.branch.id != manifest.branch_id
        or foundation.branch.tenant_id != manifest.tenant_id
        or foundation.register_snapshot.id != manifest.register_id
        or foundation.register_snapshot.tenant_id != manifest.tenant_id
        or foundation.register_snapshot.branch_id != manifest.branch_id
    ):
        raise ActivationBootstrapValidationError("Foundation snapshot scope does not match")

    calculated_foundation_hash = foundation_hash(foundation)
    scope = ActivationSnapshotScope(
        activation_id=manifest.activation_id,
        tenant_id=manifest.tenant_id,
        branch_id=manifest.branch_id,
        edge_node_id=manifest.edge_node_id,
        register_id=manifest.register_id,
        writer_epoch=manifest.writer_epoch,
        previous_writer_epoch=manifest.previous_writer_epoch,
        previous_terminal_sequence=manifest.previous_terminal_sequence,
        previous_terminal_source_checksum=manifest.previous_terminal_source_checksum,
        previous_terminal_projection_checksum=(manifest.previous_terminal_projection_checksum),
        receipt_baseline_seq=manifest.receipt_baseline_seq,
    )
    if not hmac.compare_digest(calculated_foundation_hash, manifest.foundation_hash):
        raise ActivationBootstrapValidationError("Foundation snapshot hash does not match")
    if not hmac.compare_digest(
        snapshot_hash(scope=scope, foundation_digest=calculated_foundation_hash),
        manifest.snapshot_hash,
    ):
        raise ActivationBootstrapValidationError("Activation snapshot hash does not match")
    return manifest
