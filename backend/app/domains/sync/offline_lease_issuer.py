"""Cloud lease issuance algorithm; not connected to routes or runtime admission."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from Crypto.Signature import eddsa

from app.domains.sync.offline_auth import (
    OFFLINE_AUTH_UNAVAILABLE_MESSAGE,
    OfflineAuthUnavailableError,
)
from app.domains.sync.offline_auth_lease import (
    offline_auth_lease_claims_bytes,
    offline_auth_lease_claims_hash,
)
from app.domains.sync.offline_lease_issuance_state import (
    CloudOfflineLeaseIssuanceProviderV1,
    CloudOfflineLeaseIssuanceStateV1,
)
from app.domains.sync.offline_lease_signature import Ed25519OfflineLeaseSignatureVerifier
from app.domains.sync.offline_tenant_access import permitted_offline_commands
from app.domains.sync.schemas import (
    OfflineAuthLeaseClaimsV1,
    OfflineAuthScopeV0,
    SignedOfflineAuthLeaseV1,
)


def _require_utc(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Invalid Cloud issuance time")


class CloudOfflineLeaseIssuerV1:
    """Construct claims only from a trusted current Cloud authorization snapshot.

    Private seed and key validity are explicit trusted Cloud configuration, not
    caller input. Never deliver the seed to Edge or retain it in repr/log output.
    A production provider and protected key provisioning remain prerequisites.
    """

    def __init__(
        self,
        provider: CloudOfflineLeaseIssuanceProviderV1,
        *,
        signing_key_id: UUID,
        private_seed: bytes,
        key_valid_from: datetime,
        key_valid_until: datetime,
    ) -> None:
        try:
            _require_utc(key_valid_from)
            _require_utc(key_valid_until)
            if (
                key_valid_from >= key_valid_until
                or type(private_seed) is not bytes
                or len(private_seed) != 32
            ):
                raise ValueError("Invalid Cloud signing configuration")
            key = eddsa.import_private_key(private_seed)
            public_key = key.public_key().export_key(format="raw")
            if not isinstance(public_key, bytes):
                raise ValueError("Invalid Cloud signing configuration")
            self._verifier = Ed25519OfflineLeaseSignatureVerifier({signing_key_id: public_key})
            self._signer = eddsa.new(key, "rfc8032")
            self._key_id = signing_key_id
            self._valid_from = key_valid_from
            self._valid_until = key_valid_until
            self._provider = provider
        except Exception:
            raise ValueError("Cloud offline signing configuration is invalid") from None

    async def issue(self, scope: OfflineAuthScopeV0) -> SignedOfflineAuthLeaseV1:
        """A selector supplies no rights, revisions, commands, IDs or expiry overrides."""
        try:
            selected = OfflineAuthScopeV0.model_validate(
                scope.model_dump(mode="python", warnings="error")
            )
            issued: SignedOfflineAuthLeaseV1 | None = None
            async with self._provider.serialized() as transaction:
                raw_state = await transaction.read_current_authorization(selected)
                if raw_state is None:
                    raise ValueError("Cloud admission unavailable")
                state = CloudOfflineLeaseIssuanceStateV1.model_validate(
                    raw_state.model_dump(mode="python", warnings="error")
                )
                if state.status != "active" or state.scope != selected:
                    raise ValueError("Cloud admission unavailable")
                now = await transaction.read_cloud_time()
                _require_utc(now)
                commands = permitted_offline_commands(
                    state.tenant_access_policy,
                    tenant_id=selected.tenant_id,
                    commands=state.allowed_commands,
                    now=now,
                )
                if not commands:
                    raise ValueError("Tenant policy does not admit any candidate command")
                if not self._valid_from <= now < self._valid_until:
                    raise ValueError("Cloud signing key is not current")
                deadline = min(
                    now + timedelta(days=30),
                    state.admission_expires_at,
                    state.credential_expires_at,
                    state.device_expires_at,
                    state.policy_expires_at,
                    self._valid_until,
                )
                if deadline <= now:
                    raise ValueError("Cloud admission has expired")
                claims = OfflineAuthLeaseClaimsV1(
                    schema_version=1,
                    lease_version=1,
                    lease_id=uuid4(),
                    issuer="aurum-cloud",
                    audience="aurum-edge-offline-auth-v1",
                    auth_context="owner-approved-local-credential",
                    scope=state.scope,
                    device_binding=state.device_binding,
                    allowed_commands=commands,
                    policy_revision=state.policy_revision,
                    tenant_access_revision=state.tenant_access_policy.version,
                    subject_revision=state.subject_revision,
                    admission=state.admission,
                    local_credential=state.local_credential,
                    credential_audience="aurum-edge-local-auth-v1",
                    reauthentication_policy=state.reauthentication_policy,
                    issued_at=now,
                    expires_at=deadline,
                )
                signing_bytes = offline_auth_lease_claims_bytes(claims)
                signature = self._signer.sign(signing_bytes)
                if not isinstance(signature, bytes):
                    raise ValueError("Cloud signing failed")
                envelope = SignedOfflineAuthLeaseV1(
                    claims=claims,
                    claims_hash=offline_auth_lease_claims_hash(claims),
                    signing_key_id=self._key_id,
                    signature_algorithm="ed25519-v1",
                    signature=signature.hex(),
                )
                await self._verifier.verify(envelope, signing_bytes)
                await transaction.record_issued_lease(envelope)
                await transaction.commit()
                issued = envelope
            if issued is None:
                raise ValueError("Cloud issuance was not committed")
            return issued
        except Exception:
            raise OfflineAuthUnavailableError(OFFLINE_AUTH_UNAVAILABLE_MESSAGE) from None
