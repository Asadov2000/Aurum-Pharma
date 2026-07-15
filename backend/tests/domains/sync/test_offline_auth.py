"""Offline-auth v0 contracts remain strict and runtime-denied."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.sync.offline_auth import (
    OfflineAuthContractError,
    OfflineAuthUnavailableError,
    assert_offline_auth_claims_hash,
    offline_auth_claims_hash,
    runtime_offline_auth_verifier,
)
from app.domains.sync.schemas import (
    OfflineAuthDeviceBindingV0,
    OfflineAuthGrantClaimsV0,
    OfflineAuthPayloadV0,
    OfflineAuthScopeV0,
    OfflinePosCommand,
    SignedOfflineAuthGrantV0,
)

AUTHENTICATED_AT = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
GRANT_ID_A = UUID("10000000-0000-4000-8000-000000000001")
GRANT_ID_B = UUID("10000000-0000-4000-8000-000000000002")


def _claims(
    *,
    grant_id: UUID = GRANT_ID_A,
    allowed_commands: tuple[OfflinePosCommand, ...] = (
        "operation.result.read",
        "sale.cash.complete",
    ),
    authenticated_at: datetime = AUTHENTICATED_AT,
    issued_at: datetime = AUTHENTICATED_AT + timedelta(minutes=5),
    expires_at: datetime = AUTHENTICATED_AT + timedelta(hours=72),
) -> OfflineAuthGrantClaimsV0:
    return OfflineAuthGrantClaimsV0(
        schema_version=1,
        grant_id=grant_id,
        issuer="aurum-cloud",
        audience="aurum-edge-offline-auth-v0",
        auth_context="fresh-online-interactive",
        scope=OfflineAuthScopeV0(
            activation_id=UUID("20000000-0000-4000-8000-000000000001"),
            tenant_id=UUID("30000000-0000-4000-8000-000000000001"),
            branch_id=UUID("40000000-0000-4000-8000-000000000001"),
            edge_node_id=UUID("50000000-0000-4000-8000-000000000001"),
            register_id=UUID("60000000-0000-4000-8000-000000000001"),
            writer_epoch=7,
            user_id=UUID("70000000-0000-4000-8000-000000000001"),
            capability="cash_sale_v1",
        ),
        allowed_commands=allowed_commands,
        device_binding=OfflineAuthDeviceBindingV0(
            method="tpm2",
            key_id=UUID("80000000-0000-4000-8000-000000000001"),
            spki_sha256="1" * 64,
        ),
        policy_revision=11,
        subject_revision=13,
        authenticated_at=authenticated_at,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def _signed(claims: OfflineAuthGrantClaimsV0) -> SignedOfflineAuthGrantV0:
    return SignedOfflineAuthGrantV0(
        claims=claims,
        claims_hash=offline_auth_claims_hash(claims),
        signing_key_id=UUID("90000000-0000-4000-8000-000000000001"),
        signature_algorithm="ed25519-v1",
        signature="ab" * 64,
    )


def test_offline_auth_claims_have_stable_domain_separated_digest() -> None:
    claims = _claims()

    assert offline_auth_claims_hash(claims) == (
        "0899bed65cd3cd1f0df801dbef39b74e747636dbf1bfcdb5aabcf94e2728c930"
    )


def test_offline_auth_accepts_exact_freshness_and_lifetime_boundaries() -> None:
    claims = _claims()

    assert claims.issued_at - claims.authenticated_at == timedelta(minutes=5)
    assert claims.expires_at - claims.authenticated_at == timedelta(hours=72)


@pytest.mark.parametrize(
    ("issued_at", "expires_at"),
    [
        (
            AUTHENTICATED_AT + timedelta(minutes=5, microseconds=1),
            AUTHENTICATED_AT + timedelta(hours=1),
        ),
        (AUTHENTICATED_AT, AUTHENTICATED_AT),
        (AUTHENTICATED_AT, AUTHENTICATED_AT + timedelta(hours=72, microseconds=1)),
    ],
)
def test_offline_auth_rejects_invalid_time_windows(
    issued_at: datetime,
    expires_at: datetime,
) -> None:
    with pytest.raises(ValidationError):
        _claims(issued_at=issued_at, expires_at=expires_at)


def test_offline_auth_rejects_non_utc_timestamps() -> None:
    non_utc = AUTHENTICATED_AT.astimezone(timezone(timedelta(hours=5)))

    with pytest.raises(ValidationError):
        _claims(authenticated_at=non_utc)


@pytest.mark.parametrize(
    "commands",
    [
        (),
        ("sale.cash.complete", "sale.cash.complete"),
        ("sale.cash.complete", "operation.result.read"),
        ("pos.sell",),
    ],
)
def test_offline_auth_rejects_noncanonical_commands(commands: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        OfflineAuthGrantClaimsV0.model_validate(
            {
                **_claims().model_dump(mode="python"),
                "allowed_commands": commands,
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("password_hash", "forbidden"),
        ("refresh_token", "forbidden"),
        ("permissions", ("pos.sell",)),
    ],
)
def test_offline_auth_rejects_sensitive_or_unexpected_fields(field: str, value: object) -> None:
    payload = _claims().model_dump(mode="python")
    payload[field] = value

    with pytest.raises(ValidationError):
        OfflineAuthGrantClaimsV0.model_validate(payload)


def test_offline_auth_rejects_coerced_revisions() -> None:
    payload = _claims().model_dump(mode="python")
    payload["policy_revision"] = "11"

    with pytest.raises(ValidationError):
        OfflineAuthGrantClaimsV0.model_validate(payload)


def test_offline_auth_rejects_development_hmac_algorithm() -> None:
    payload = _signed(_claims()).model_dump(mode="python")
    payload["signature_algorithm"] = "hmac-sha256-edge-v1"

    with pytest.raises(ValidationError):
        SignedOfflineAuthGrantV0.model_validate(payload)


def test_offline_auth_detects_claim_tampering() -> None:
    grant = _signed(_claims())
    tampered_claims = _claims(allowed_commands=("receipt.reprint",))
    tampered = SignedOfflineAuthGrantV0(
        claims=tampered_claims,
        claims_hash=grant.claims_hash,
        signing_key_id=grant.signing_key_id,
        signature_algorithm="ed25519-v1",
        signature=grant.signature,
    )

    with pytest.raises(OfflineAuthContractError):
        assert_offline_auth_claims_hash(tampered)


def test_offline_auth_payload_requires_unique_canonical_grant_order() -> None:
    grant_a = _signed(_claims(grant_id=GRANT_ID_A))
    grant_b = _signed(_claims(grant_id=GRANT_ID_B))

    payload = OfflineAuthPayloadV0(
        schema_version=1,
        component="offline_auth",
        grants=(grant_a, grant_b),
    )
    assert len(payload.grants) == 2

    with pytest.raises(ValidationError):
        OfflineAuthPayloadV0(
            schema_version=1,
            component="offline_auth",
            grants=(grant_b, grant_a),
        )
    with pytest.raises(ValidationError):
        OfflineAuthPayloadV0(
            schema_version=1,
            component="offline_auth",
            grants=(grant_a, grant_a),
        )


async def test_runtime_offline_auth_is_unconditionally_denied() -> None:
    verifier = runtime_offline_auth_verifier()

    with pytest.raises(OfflineAuthUnavailableError, match="unavailable"):
        await verifier.authorize(_signed(_claims()), "sale.cash.complete")
