"""Lease v1 is a strict signed contract, never runtime offline authorization."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.sync.offline_auth import (
    OfflineAuthContractError,
    OfflineAuthUnavailableError,
    offline_auth_claims_bytes,
)
from app.domains.sync.offline_auth_lease import (
    assert_offline_auth_lease_claims_hash,
    offline_auth_lease_claims_bytes,
    offline_auth_lease_claims_hash,
    runtime_offline_auth_lease_verifier,
)
from app.domains.sync.schemas import (
    OfflineAuthDeviceBindingV0,
    OfflineAuthGrantClaimsV0,
    OfflineAuthLeaseClaimsV1,
    OfflineAuthLeaseReferenceV1,
    OfflineAuthScopeV0,
    OfflinePosCommand,
    SignedOfflineAuthGrantV0,
    SignedOfflineAuthLeaseV1,
)

ISSUED_AT = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)
LEASE_DOMAIN = b"aurum:offline-auth-lease:v1\0"
COMMANDS: tuple[OfflinePosCommand, ...] = (
    "operation.result.read",
    "receipt.print",
    "receipt.reprint",
    "sale.cash.complete",
    "shift.close",
    "shift.open",
)


def _uuid(number: int) -> UUID:
    return UUID(f"{number:08x}-0000-4000-8000-000000000001")


def _claims() -> OfflineAuthLeaseClaimsV1:
    return OfflineAuthLeaseClaimsV1(
        schema_version=1,
        lease_version=1,
        lease_id=_uuid(1),
        issuer="aurum-cloud",
        audience="aurum-edge-offline-auth-v1",
        auth_context="owner-approved-local-credential",
        scope=OfflineAuthScopeV0(
            activation_id=_uuid(2),
            tenant_id=_uuid(3),
            branch_id=_uuid(4),
            edge_node_id=_uuid(5),
            register_id=_uuid(6),
            writer_epoch=7,
            user_id=_uuid(8),
            capability="cash_sale_v1",
        ),
        allowed_commands=COMMANDS,
        device_binding=OfflineAuthDeviceBindingV0(
            method="tpm2", key_id=_uuid(9), spki_sha256="ab" * 32
        ),
        policy_revision=11,
        subject_revision=13,
        admission=OfflineAuthLeaseReferenceV1(reference_id=_uuid(14), revision=1),
        local_credential=OfflineAuthLeaseReferenceV1(reference_id=_uuid(15), revision=2),
        reauthentication_policy=OfflineAuthLeaseReferenceV1(reference_id=_uuid(16), revision=3),
        credential_audience="aurum-edge-local-auth-v1",
        issued_at=ISSUED_AT,
        expires_at=ISSUED_AT + timedelta(days=30),
    )


def _changed(path: str, value: object) -> OfflineAuthLeaseClaimsV1:
    payload = _claims().model_dump(mode="python")
    parent, separator, field = path.partition(".")
    if separator:
        cast(dict[str, object], payload[parent])[field] = value
    else:
        payload[parent] = value
    return OfflineAuthLeaseClaimsV1.model_validate(payload)


def _signed(claims: OfflineAuthLeaseClaimsV1) -> SignedOfflineAuthLeaseV1:
    return SignedOfflineAuthLeaseV1(
        claims=claims,
        claims_hash=offline_auth_lease_claims_hash(claims),
        signing_key_id=_uuid(17),
        signature_algorithm="ed25519-v1",
        signature="cd" * 64,
    )


def _v0_claims() -> OfflineAuthGrantClaimsV0:
    lease = _claims()
    return OfflineAuthGrantClaimsV0(
        schema_version=1,
        grant_id=lease.lease_id,
        issuer="aurum-cloud",
        audience="aurum-edge-offline-auth-v0",
        auth_context="fresh-online-interactive",
        scope=lease.scope,
        allowed_commands=lease.allowed_commands,
        device_binding=lease.device_binding,
        policy_revision=lease.policy_revision,
        subject_revision=lease.subject_revision,
        authenticated_at=ISSUED_AT,
        issued_at=ISSUED_AT,
        expires_at=ISSUED_AT + timedelta(hours=72),
    )


def test_exact_thirty_day_boundary_and_json_round_trip() -> None:
    claims = _claims()
    assert claims.expires_at - claims.issued_at == timedelta(days=30)
    assert OfflineAuthLeaseClaimsV1.model_validate_json(claims.model_dump_json()) == claims


@pytest.mark.parametrize(
    "expires_at",
    [ISSUED_AT + timedelta(days=30, microseconds=1), ISSUED_AT, ISSUED_AT - timedelta(seconds=1)],
)
def test_invalid_lease_window_is_rejected(expires_at: datetime) -> None:
    with pytest.raises(ValidationError):
        _changed("expires_at", expires_at)


@pytest.mark.parametrize("field", ["issued_at", "expires_at"])
@pytest.mark.parametrize("offset", [None, timezone(timedelta(hours=5))])
def test_lease_timestamps_must_be_explicit_utc(field: str, offset: timezone | None) -> None:
    timestamp = _claims().issued_at if field == "issued_at" else _claims().expires_at
    value = timestamp.replace(tzinfo=None) if offset is None else timestamp.astimezone(offset)
    with pytest.raises(ValidationError):
        _changed(field, value)


@pytest.mark.parametrize(
    "commands",
    [
        (),
        ("pos.sell",),
        ("receipt.print", "receipt.print"),
        tuple(reversed(COMMANDS)),
        COMMANDS * 2,
    ],
)
def test_commands_are_known_unique_bounded_and_sorted(commands: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        _changed("allowed_commands", commands)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("lease_version", 0),
        ("audience", "aurum-edge-offline-auth-v0"),
        ("issuer", "local-edge"),
        ("auth_context", "fresh-online-interactive"),
        ("credential_audience", "aurum-cloud"),
        ("scope.capability", "pos.sell"),
        ("device_binding.method", "software"),
        ("password_hash", "synthetic-forbidden"),
        ("refresh_token", "synthetic-forbidden"),
        ("permissions", ("pos.sell",)),
        ("admission.owner_email", "fixture@example.invalid"),
        ("local_credential.secret", "synthetic-forbidden"),
        ("reauthentication_policy.password", "synthetic-forbidden"),
    ],
)
def test_strict_identity_and_unknown_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _changed(field, value)


@pytest.mark.parametrize(
    "field",
    [
        "policy_revision",
        "subject_revision",
        "scope.writer_epoch",
        "admission.revision",
        "local_credential.revision",
        "reauthentication_policy.revision",
    ],
)
@pytest.mark.parametrize("value", [0, -1, True, "1"])
def test_revisions_are_positive_integers_without_coercion(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _changed(field, value)


def test_v0_and_v1_envelopes_reject_each_other() -> None:
    lease = _signed(_claims())
    grant_claims = _v0_claims()
    grant = SignedOfflineAuthGrantV0(
        claims=grant_claims,
        claims_hash=hashlib.sha256(offline_auth_claims_bytes(grant_claims)).hexdigest(),
        signing_key_id=lease.signing_key_id,
        signature_algorithm="ed25519-v1",
        signature=lease.signature,
    )
    with pytest.raises(ValidationError):
        SignedOfflineAuthGrantV0.model_validate_json(lease.model_dump_json())
    with pytest.raises(ValidationError):
        SignedOfflineAuthLeaseV1.model_validate_json(grant.model_dump_json())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("signature_algorithm", "hmac-sha256-edge-v1"),
        ("signature", "cd" * 63),
        ("signature", "zz" * 64),
        ("claims_hash", "not-a-sha256"),
        ("refresh_token", "synthetic-forbidden"),
    ],
)
def test_envelope_rejects_weakened_signatures_and_extra_secrets(field: str, value: object) -> None:
    payload = _signed(_claims()).model_dump(mode="python")
    payload[field] = value
    with pytest.raises(ValidationError):
        SignedOfflineAuthLeaseV1.model_validate(payload)


@pytest.mark.parametrize("construction", ["model_copy", "model_construct"])
def test_digest_helpers_revalidate_instances_built_without_validation(construction: str) -> None:
    claims = _claims()
    invalid_expiry = claims.expires_at + timedelta(microseconds=1)
    if construction == "model_copy":
        invalid = claims.model_copy(update={"expires_at": invalid_expiry})
    else:
        payload = dict(claims)
        payload["expires_at"] = invalid_expiry
        invalid = OfflineAuthLeaseClaimsV1.model_construct(**payload)
    with pytest.raises(ValidationError):
        offline_auth_lease_claims_bytes(invalid)
    with pytest.raises(ValidationError):
        offline_auth_lease_claims_hash(invalid)
    invalid_envelope = _signed(claims).model_copy(update={"claims": invalid})
    with pytest.raises(ValidationError):
        assert_offline_auth_lease_claims_hash(invalid_envelope)


def test_signing_bytes_are_canonical_and_separate_from_v0() -> None:
    claims = _claims()
    canonical_json = json.dumps(
        claims.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    expected = LEASE_DOMAIN + canonical_json
    assert offline_auth_lease_claims_bytes(claims) == expected
    assert offline_auth_lease_claims_hash(claims) == hashlib.sha256(expected).hexdigest()
    assert not offline_auth_claims_bytes(_v0_claims()).startswith(LEASE_DOMAIN)
    assert_offline_auth_lease_claims_hash(_signed(claims))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lease_id", _uuid(99)),
        ("scope.activation_id", _uuid(99)),
        ("scope.tenant_id", _uuid(99)),
        ("scope.branch_id", _uuid(99)),
        ("scope.edge_node_id", _uuid(99)),
        ("scope.register_id", _uuid(99)),
        ("scope.user_id", _uuid(99)),
        ("scope.writer_epoch", 8),
        ("device_binding.key_id", _uuid(99)),
        ("device_binding.spki_sha256", "ef" * 32),
        ("policy_revision", 12),
        ("subject_revision", 14),
        ("admission.reference_id", _uuid(99)),
        ("local_credential.reference_id", _uuid(99)),
        ("reauthentication_policy.reference_id", _uuid(99)),
        ("admission.revision", 99),
        ("local_credential.revision", 99),
        ("reauthentication_policy.revision", 99),
        ("allowed_commands", ("receipt.print",)),
        ("issued_at", ISSUED_AT + timedelta(microseconds=1)),
        ("expires_at", ISSUED_AT + timedelta(days=29)),
    ],
)
def test_every_mutable_authority_field_is_committed_to_digest(field: str, value: object) -> None:
    original = _signed(_claims())
    changed = _changed(field, value)
    assert offline_auth_lease_claims_hash(changed) != original.claims_hash
    payload = original.model_dump(mode="python")
    payload["claims"] = changed
    with pytest.raises(OfflineAuthContractError):
        assert_offline_auth_lease_claims_hash(SignedOfflineAuthLeaseV1.model_validate(payload))


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("case", ["current", "future", "expired", "wrong_identity"])
async def test_runtime_never_authorizes_a_lease(case: str, command: OfflinePosCommand) -> None:
    now = datetime.now(UTC)
    payload = _claims().model_dump(mode="python")
    offset = {"current": -1, "future": 24, "expired": -72, "wrong_identity": -1}[case]
    payload["issued_at"] = now + timedelta(hours=offset)
    payload["expires_at"] = now + timedelta(hours=offset + 48)
    if case == "wrong_identity":
        cast(dict[str, object], payload["scope"])["user_id"] = _uuid(99)
    lease = _signed(OfflineAuthLeaseClaimsV1.model_validate(payload))
    assert_offline_auth_lease_claims_hash(lease)
    with pytest.raises(OfflineAuthUnavailableError):
        await runtime_offline_auth_lease_verifier().authorize(lease, command)
