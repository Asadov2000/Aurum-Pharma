"""Ed25519 lease signatures require explicit Cloud trust and exact v1 bytes."""

from __future__ import annotations

import hashlib
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from Crypto.Hash import SHA512
from Crypto.PublicKey import ECC
from Crypto.PublicKey.ECC import EccKey
from Crypto.Signature import eddsa

from app.domains.sync.offline_auth import (
    OFFLINE_AUTH_UNAVAILABLE_MESSAGE,
    OfflineAuthUnavailableError,
)
from app.domains.sync.offline_auth_lease import (
    DenyAllOfflineLeaseVerifierV1,
    offline_auth_lease_claims_bytes,
    offline_auth_lease_claims_hash,
    runtime_offline_auth_lease_verifier,
)
from app.domains.sync.offline_lease_signature import (
    Ed25519OfflineLeaseSignatureVerifier,
    _verify_ed25519,
)
from app.domains.sync.schemas import (
    OfflineAuthDeviceBindingV0,
    OfflineAuthLeaseClaimsV1,
    OfflineAuthLeaseReferenceV1,
    OfflineAuthScopeV0,
    SignedOfflineAuthLeaseV1,
)

ISSUED_AT = datetime(2026, 9, 26, 9, tzinfo=UTC)
ED25519_ORDER = 2**252 + 27742317777372353535851937790883648493
ED25519_PRIME = 2**255 - 19
INVALID_KEYS_MESSAGE = "Trusted offline signing keys are invalid"


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
        allowed_commands=("receipt.print", "sale.cash.complete"),
        device_binding=OfflineAuthDeviceBindingV0(
            method="tpm2", key_id=_uuid(9), spki_sha256="ab" * 32
        ),
        policy_revision=11,
        tenant_access_revision=7,
        subject_revision=13,
        admission=OfflineAuthLeaseReferenceV1(reference_id=_uuid(14), revision=1),
        local_credential=OfflineAuthLeaseReferenceV1(reference_id=_uuid(15), revision=2),
        reauthentication_policy=OfflineAuthLeaseReferenceV1(reference_id=_uuid(16), revision=3),
        credential_audience="aurum-edge-local-auth-v1",
        issued_at=ISSUED_AT,
        expires_at=ISSUED_AT + timedelta(days=30),
    )


@dataclass(frozen=True)
class _SignedLease:
    private_key: EccKey = field(repr=False)
    lease: SignedOfflineAuthLeaseV1
    signing_bytes: bytes
    public_key: bytes

    @property
    def verifier(self) -> Ed25519OfflineLeaseSignatureVerifier:
        return Ed25519OfflineLeaseSignatureVerifier({self.lease.signing_key_id: self.public_key})


def _sign(key: EccKey, message: bytes) -> str:
    return cast(bytes, eddsa.new(key, "rfc8032").sign(message)).hex()


@pytest.fixture
def signed() -> _SignedLease:
    # Generated keys exist only in memory; no private key material is serialized.
    private_key = ECC.generate(curve="Ed25519")
    claims = _claims()
    signing_bytes = offline_auth_lease_claims_bytes(claims)
    lease = SignedOfflineAuthLeaseV1(
        claims=claims,
        claims_hash=offline_auth_lease_claims_hash(claims),
        signing_key_id=_uuid(17),
        signature_algorithm="ed25519-v1",
        signature=_sign(private_key, signing_bytes),
    )
    public_key = cast(bytes, private_key.public_key().export_key(format="raw"))
    return _SignedLease(private_key, lease, signing_bytes, public_key)


async def _assert_denied(
    verifier: Ed25519OfflineLeaseSignatureVerifier,
    lease: SignedOfflineAuthLeaseV1,
    signing_bytes: bytes,
) -> None:
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        with pytest.raises(OfflineAuthUnavailableError) as error:
            await verifier.verify(lease, signing_bytes)
    assert not emitted
    assert str(error.value) == OFFLINE_AUTH_UNAVAILABLE_MESSAGE
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


async def test_valid_signature_covers_complete_canonical_contract(signed: _SignedLease) -> None:
    assert signed.signing_bytes.startswith(b"aurum:offline-auth-lease:v1\0")
    assert await signed.verifier.verify(signed.lease, signed.signing_bytes) is None
    round_trip = SignedOfflineAuthLeaseV1.model_validate_json(signed.lease.model_dump_json())
    assert await signed.verifier.verify(round_trip, signed.signing_bytes) is None


def test_rfc8032_known_pure_ed25519_vector() -> None:
    # RFC 8032 section 7.1, TEST 1; public data, empty message.
    public_key = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    assert _verify_ed25519(public_key, b"", signature) is None
    with pytest.raises(ValueError):
        _verify_ed25519(public_key, b"\0", signature)


def test_scalar_equal_to_group_order_is_rejected_before_library_equation() -> None:
    # A synthetic identity point exposes the locked library's S == L boundary:
    # its cofactored equation otherwise accepts this encoding. The public key
    # is independently forbidden by the trusted-key constructor as well.
    identity = b"\x01" + bytes(31)
    signature = identity + ED25519_ORDER.to_bytes(32, "little")
    with pytest.raises(ValueError):
        _verify_ed25519(identity, b"", signature)


@pytest.mark.parametrize(
    "keys",
    [
        pytest.param({"untrusted-id": b"x" * 32}, id="string-key-id"),
        pytest.param({UUID(int=1): b"x" * 32}, id="non-v4-key-id"),
        pytest.param({_uuid(17): "x" * 32}, id="string-public-key"),
        pytest.param({_uuid(17): bytearray(32)}, id="mutable-public-key"),
        pytest.param({_uuid(17): memoryview(bytes(32))}, id="memoryview-public-key"),
        pytest.param({_uuid(17): bytes(31)}, id="short-public-key"),
        pytest.param({_uuid(17): bytes(33)}, id="long-public-key"),
        pytest.param({_uuid(17): bytes(32)}, id="order-four-point"),
        pytest.param({_uuid(17): b"\x01" + bytes(31)}, id="identity-point"),
        pytest.param({_uuid(17): (ED25519_PRIME - 1).to_bytes(32, "little")}, id="order-two-point"),
        pytest.param({_uuid(17): (ED25519_PRIME + 1).to_bytes(32, "little")}, id="noncanonical-y"),
        pytest.param({_uuid(17): b"\xff" * 32}, id="invalid-point"),
        pytest.param(
            {_uuid(17): (1 + 2**255).to_bytes(32, "little")}, id="noncanonical-zero-x-sign"
        ),
    ],
)
def test_invalid_trusted_keys_raise_generic_configuration_error(keys: object) -> None:
    with pytest.raises(ValueError) as error:
        Ed25519OfflineLeaseSignatureVerifier(cast(Mapping[UUID, bytes], keys))
    assert str(error.value) == INVALID_KEYS_MESSAGE
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


def test_same_public_key_cannot_have_multiple_trusted_ids(signed: _SignedLease) -> None:
    with pytest.raises(ValueError, match=f"^{INVALID_KEYS_MESSAGE}$"):
        Ed25519OfflineLeaseSignatureVerifier(
            {signed.lease.signing_key_id: signed.public_key, _uuid(18): signed.public_key}
        )


@pytest.mark.parametrize("trust", ["empty", "unknown-id", "different-key"])
async def test_only_explicit_matching_trusted_key_accepts(signed: _SignedLease, trust: str) -> None:
    keys: dict[UUID, bytes] = {}
    if trust == "unknown-id":
        keys[_uuid(18)] = signed.public_key
    elif trust == "different-key":
        other_key = ECC.generate(curve="Ed25519")
        keys[signed.lease.signing_key_id] = cast(
            bytes, other_key.public_key().export_key(format="raw")
        )
    await _assert_denied(
        Ed25519OfflineLeaseSignatureVerifier(keys), signed.lease, signed.signing_bytes
    )


async def test_trusted_keyring_is_snapshot_of_caller_mapping(signed: _SignedLease) -> None:
    keys = {signed.lease.signing_key_id: signed.public_key}
    verifier = Ed25519OfflineLeaseSignatureVerifier(keys)
    keys.clear()
    keys[_uuid(18)] = signed.public_key
    assert await verifier.verify(signed.lease, signed.signing_bytes) is None
    injected_id = signed.lease.model_copy(update={"signing_key_id": _uuid(18)})
    await _assert_denied(verifier, injected_id, signed.signing_bytes)


async def test_rotation_accepts_distinct_keys_and_removal_denies_old_key(
    signed: _SignedLease,
) -> None:
    new_key = ECC.generate(curve="Ed25519")
    new_public_key = cast(bytes, new_key.public_key().export_key(format="raw"))
    new_lease = signed.lease.model_copy(
        update={"signing_key_id": _uuid(18), "signature": _sign(new_key, signed.signing_bytes)}
    )
    rotating = Ed25519OfflineLeaseSignatureVerifier(
        {signed.lease.signing_key_id: signed.public_key, _uuid(18): new_public_key}
    )
    assert await rotating.verify(signed.lease, signed.signing_bytes) is None
    assert await rotating.verify(new_lease, signed.signing_bytes) is None
    rotated = Ed25519OfflineLeaseSignatureVerifier({_uuid(18): new_public_key})
    await _assert_denied(rotated, signed.lease, signed.signing_bytes)
    assert await rotated.verify(new_lease, signed.signing_bytes) is None


@pytest.mark.parametrize(
    "kind", ["bytearray", "digest", "hex-digest", "no-domain", "v0-domain", "appended"]
)
async def test_caller_bytes_must_equal_exact_full_v1_canonical_bytes(
    signed: _SignedLease, kind: str
) -> None:
    candidate: object
    if kind == "bytearray":
        candidate = bytearray(signed.signing_bytes)
    elif kind == "digest":
        candidate = hashlib.sha256(signed.signing_bytes).digest()
    elif kind == "hex-digest":
        candidate = signed.lease.claims_hash.encode()
    elif kind == "no-domain":
        candidate = signed.signing_bytes.split(b"\0", 1)[1]
    elif kind == "v0-domain":
        candidate = signed.signing_bytes.replace(
            b"aurum:offline-auth-lease:v1\0", b"aurum:offline-auth-grant:v0\0", 1
        )
    else:
        candidate = signed.signing_bytes + b"\0"
    await _assert_denied(signed.verifier, signed.lease, cast(bytes, candidate))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("signature_algorithm", "ed25519ph-v1"),
        ("signature", "ff" * 63),
        ("signature", bytes(64)),
        ("signing_key_id", str(_uuid(17))),
        ("claims_hash", "ab" * 32),
    ],
)
async def test_unvalidated_envelope_copies_are_revalidated(
    signed: _SignedLease, field: str, value: object
) -> None:
    tampered = signed.lease.model_copy(update={field: value})
    await _assert_denied(signed.verifier, tampered, signed.signing_bytes)


async def test_nested_claims_are_strictly_revalidated(signed: _SignedLease) -> None:
    scope = signed.lease.claims.scope.model_copy(update={"writer_epoch": "7"})
    claims = signed.lease.claims.model_copy(update={"scope": scope})
    tampered = signed.lease.model_copy(update={"claims": claims})
    await _assert_denied(signed.verifier, tampered, signed.signing_bytes)


@pytest.mark.parametrize("revision", ["subject_revision", "tenant_access_revision"])
async def test_modified_claims_still_deny_after_rehash(signed: _SignedLease, revision: str) -> None:
    claims = signed.lease.claims.model_copy(update={revision: 14})
    tampered = signed.lease.model_copy(
        update={"claims": claims, "claims_hash": offline_auth_lease_claims_hash(claims)}
    )
    await _assert_denied(signed.verifier, tampered, offline_auth_lease_claims_bytes(claims))


@pytest.mark.parametrize("signed_content", ["digest", "v0-domain", "ed25519ph"])
async def test_signatures_from_other_message_domains_or_modes_deny(
    signed: _SignedLease, signed_content: str
) -> None:
    if signed_content == "ed25519ph":
        signature = cast(
            bytes, eddsa.new(signed.private_key, "rfc8032").sign(SHA512.new(signed.signing_bytes))
        ).hex()
    else:
        message = (
            hashlib.sha256(signed.signing_bytes).digest()
            if signed_content == "digest"
            else signed.signing_bytes.replace(
                b"aurum:offline-auth-lease:v1\0", b"aurum:offline-auth-grant:v0\0", 1
            )
        )
        signature = _sign(signed.private_key, message)
    tampered = signed.lease.model_copy(update={"signature": signature})
    await _assert_denied(signed.verifier, tampered, signed.signing_bytes)


@pytest.mark.parametrize("part", ["equation", "scalar-order", "noncanonical-r"])
async def test_malformed_or_malleable_signature_denies(signed: _SignedLease, part: str) -> None:
    signature = bytearray.fromhex(signed.lease.signature)
    if part == "equation":
        signature[0] ^= 1
    elif part == "scalar-order":
        scalar = int.from_bytes(signature[32:], "little") + ED25519_ORDER
        signature[32:] = scalar.to_bytes(32, "little")
    else:
        signature[:32] = (ED25519_PRIME + 1).to_bytes(32, "little")
    tampered = signed.lease.model_copy(update={"signature": signature.hex()})
    await _assert_denied(signed.verifier, tampered, signed.signing_bytes)


async def test_runtime_remains_deny_all_with_cryptographically_valid_lease(
    signed: _SignedLease,
) -> None:
    assert await signed.verifier.verify(signed.lease, signed.signing_bytes) is None
    runtime = runtime_offline_auth_lease_verifier()
    assert isinstance(runtime, DenyAllOfflineLeaseVerifierV1)
    with pytest.raises(OfflineAuthUnavailableError) as error:
        await runtime.authorize(signed.lease, "sale.cash.complete")
    assert str(error.value) == OFFLINE_AUTH_UNAVAILABLE_MESSAGE
