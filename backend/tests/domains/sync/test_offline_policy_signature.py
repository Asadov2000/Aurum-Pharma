"""Real Ed25519 policy deliveries remain strict and separately trust nested leases."""

from __future__ import annotations

import hashlib
import warnings
from datetime import timedelta, timezone
from typing import cast

import pytest
from Crypto.Hash import SHA512
from Crypto.Signature import eddsa
from pydantic import ValidationError

from app.domains.sync.offline_auth import (
    OFFLINE_AUTH_UNAVAILABLE_MESSAGE,
    OfflineAuthUnavailableError,
)
from app.domains.sync.offline_auth_lease import (
    offline_auth_lease_claims_bytes,
    offline_auth_lease_claims_hash,
)
from app.domains.sync.offline_policy_signature import (
    Ed25519OfflinePolicyDeliveryVerifier,
    offline_policy_delivery_bytes,
    offline_policy_delivery_hash,
)
from app.domains.sync.schemas import SignedOfflinePolicyDeliveryV1
from tests.domains.sync.offline_policy_fixtures import (
    NOW,
    SigningFixture,
    claims,
    lease_claims,
    policy,
    uuid,
)


@pytest.fixture
def signed() -> SigningFixture:
    return SigningFixture()


async def _assert_denied(
    verifier: Ed25519OfflinePolicyDeliveryVerifier,
    delivery: SignedOfflinePolicyDeliveryV1,
) -> None:
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        with pytest.raises(OfflineAuthUnavailableError) as error:
            await verifier.verify(delivery)
    assert not emitted
    assert str(error.value) == OFFLINE_AUTH_UNAVAILABLE_MESSAGE
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


@pytest.mark.parametrize("with_lease", [False, True])
async def test_real_signatures_and_json_round_trip(
    signed: SigningFixture, with_lease: bool
) -> None:
    delivery = signed.signed_delivery(with_lease=with_lease)
    assert offline_policy_delivery_bytes(delivery.claims).startswith(
        b"aurum-edge-policy-delivery-v1\x00"
    )
    await signed.verifier.verify(delivery)
    round_trip = SignedOfflinePolicyDeliveryV1.model_validate_json(delivery.model_dump_json())
    assert round_trip == delivery
    await signed.verifier.verify(round_trip)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("signature_algorithm",), "ed25519ph-v1"),
        (("signature",), "ab" * 63),
        (("signature",), "AB" * 64),
        (("claims_hash",), "AB" * 32),
        (("signing_key_id",), str(uuid(17))),
        (("claims", "schema_version"), 2),
        (("claims", "issuer"), "edge"),
        (("claims", "audience"), "aurum-edge-offline-auth-v1"),
        (("claims", "delivery_sequence"), "1"),
        (("claims", "delivery_sequence"), True),
        (("claims", "delivery_sequence"), 0),
        (("claims", "delivery_sequence"), 2**63),
        (("claims", "delivery_id"), uuid(19).bytes),
        (("claims", "issued_at"), NOW.replace(tzinfo=None)),
        (("claims", "issued_at"), NOW.astimezone(timezone(timedelta(hours=3)))),
        (("claims", "scope", "writer_epoch"), "6"),
        (("claims", "scope", "capability"), "inventory"),
        (("claims", "scope", "tenant_id"), uuid(99)),
        (("claims", "scope", "activation_id"), "00000000-0000-1000-8000-000000000001"),
        (("claims", "device_binding", "method"), "software"),
        (("claims", "device_binding", "spki_sha256"), "AB" * 32),
        (("claims", "policy", "version"), "7"),
        (("claims", "policy", "blocked_modules"), ("pos", "inventory")),
        (("claims", "policy", "blocked_modules"), ("pos", "pos")),
        (("claims", "policy", "expires_at"), NOW.replace(tzinfo=None)),
        (
            ("claims", "lease", "claims", "allowed_commands"),
            ("sale.cash.complete", "receipt.print"),
        ),
    ],
)
def test_wire_contract_rejects_coercion_or_noncanonical_values(
    signed: SigningFixture, path: tuple[str, ...], value: object
) -> None:
    payload = signed.signed_delivery(with_lease=True).model_dump(mode="python")
    target = payload
    for key in path[:-1]:
        target = cast(dict[str, object], target[key])
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        SignedOfflinePolicyDeliveryV1.model_validate(payload)


@pytest.mark.parametrize("path", [(), ("claims",), ("claims", "scope"), ("claims", "policy")])
def test_wire_contract_forbids_extra_embedded_keys_and_secrets(
    signed: SigningFixture, path: tuple[str, ...]
) -> None:
    payload = signed.signed_delivery().model_dump(mode="python")
    target = payload
    for key in path:
        target = cast(dict[str, object], target[key])
    target["embedded_signing_key"] = "untrusted-placeholder"
    with pytest.raises(ValidationError):
        SignedOfflinePolicyDeliveryV1.model_validate(payload)


def test_explicit_null_lease_is_required_on_wire(signed: SigningFixture) -> None:
    payload = signed.signed_delivery().model_dump(mode="python")
    cast(dict[str, object], payload["claims"]).pop("lease")
    with pytest.raises(ValidationError):
        SignedOfflinePolicyDeliveryV1.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    ["activation_id", "tenant_id", "branch_id", "edge_node_id", "register_id", "writer_epoch"],
)
def test_nested_lease_must_match_every_target_scope_field(
    signed: SigningFixture, field: str
) -> None:
    value = lease_claims()
    different_scope = value.scope.model_copy(
        update={field: 7 if field == "writer_epoch" else uuid(99)}
    )
    lease = signed.sign_lease(value.model_copy(update={"scope": different_scope}))
    with pytest.raises(ValidationError, match="Policy delivery lease mismatch"):
        claims(lease=lease)


@pytest.mark.parametrize("field", ["key_id", "spki_sha256"])
def test_nested_lease_must_match_device_binding(signed: SigningFixture, field: str) -> None:
    value = lease_claims()
    different_binding = value.device_binding.model_copy(
        update={field: uuid(99) if field == "key_id" else "cd" * 32}
    )
    lease = signed.sign_lease(value.model_copy(update={"device_binding": different_binding}))
    with pytest.raises(ValidationError, match="Policy delivery lease mismatch"):
        claims(lease=lease)


def test_nested_lease_revision_and_issue_time_must_match_delivery(signed: SigningFixture) -> None:
    with pytest.raises(ValidationError, match="Policy delivery lease mismatch"):
        claims(lease=signed.sign_lease(lease_claims(version=8)))
    future = lease_claims().model_copy(update={"issued_at": NOW + timedelta(seconds=1)})
    with pytest.raises(ValidationError, match="Policy delivery lease mismatch"):
        claims(lease=signed.sign_lease(future))


@pytest.mark.parametrize(
    "field", ["delivery_id", "delivery_sequence", "scope", "device_binding", "policy", "issued_at"]
)
async def test_tampering_is_denied_even_after_rehash(signed: SigningFixture, field: str) -> None:
    delivery = signed.signed_delivery()
    value = delivery.claims
    updates: dict[str, object] = {
        "delivery_id": uuid(99),
        "delivery_sequence": 2,
        "scope": value.scope.model_copy(update={"writer_epoch": 7}),
        "device_binding": value.device_binding.model_copy(update={"key_id": uuid(99)}),
        "policy": value.policy.model_copy(update={"version": 8}),
        "issued_at": NOW + timedelta(seconds=1),
    }
    changed = value.model_copy(update={field: updates[field]})
    tampered = delivery.model_copy(
        update={"claims": changed, "claims_hash": offline_policy_delivery_hash(changed)}
    )
    await _assert_denied(signed.verifier, tampered)


@pytest.mark.parametrize("content", ["lease-domain", "digest", "no-domain", "ed25519ph", "context"])
async def test_signatures_for_other_domains_or_modes_are_denied(
    signed: SigningFixture, content: str
) -> None:
    delivery = signed.signed_delivery()
    message = offline_policy_delivery_bytes(delivery.claims)
    if content == "ed25519ph":
        signature = eddsa.new(signed.policy_private_key, "rfc8032").sign(SHA512.new(message)).hex()
    elif content == "context":
        signature = (
            eddsa.new(signed.policy_private_key, "rfc8032", context=b"policy").sign(message).hex()
        )
    else:
        alternate = {
            "lease-domain": offline_auth_lease_claims_bytes(lease_claims()),
            "digest": hashlib.sha256(message).digest(),
            "no-domain": message.split(b"\x00", 1)[1],
        }
        signature = signed.policy_signature(alternate[content])
    await _assert_denied(signed.verifier, delivery.model_copy(update={"signature": signature}))


@pytest.mark.parametrize(
    "trust", ["empty-policy", "empty-lease", "unknown-policy", "unknown-lease", "swapped"]
)
async def test_both_keyrings_require_explicit_matching_trust(
    signed: SigningFixture, trust: str
) -> None:
    policy_keys = signed.policy_keys
    lease_keys = signed.lease_keys
    if trust == "empty-policy":
        policy_keys = {}
    elif trust == "empty-lease":
        lease_keys = {}
    elif trust == "unknown-policy":
        policy_keys = {uuid(99): policy_keys[uuid(17)]}
    elif trust == "unknown-lease":
        lease_keys = {uuid(99): lease_keys[uuid(18)]}
    else:
        policy_keys, lease_keys = lease_keys, policy_keys
    verifier = Ed25519OfflinePolicyDeliveryVerifier(policy_keys=policy_keys, lease_keys=lease_keys)
    await _assert_denied(verifier, signed.signed_delivery(with_lease=True))


@pytest.mark.parametrize("ring", ["policy", "lease"])
def test_public_key_aliases_are_rejected_in_either_keyring(
    signed: SigningFixture, ring: str
) -> None:
    policy_keys = signed.policy_keys
    lease_keys = signed.lease_keys
    keys = policy_keys if ring == "policy" else lease_keys
    keys[uuid(99)] = next(iter(keys.values()))
    with pytest.raises(ValueError, match="^Trusted offline signing keys are invalid$"):
        Ed25519OfflinePolicyDeliveryVerifier(policy_keys=policy_keys, lease_keys=lease_keys)


async def test_keyrings_are_snapshots_and_cannot_receive_input_keys(signed: SigningFixture) -> None:
    policy_keys = signed.policy_keys
    lease_keys = signed.lease_keys
    verifier = Ed25519OfflinePolicyDeliveryVerifier(policy_keys=policy_keys, lease_keys=lease_keys)
    policy_public = policy_keys.pop(uuid(17))
    lease_keys.clear()
    policy_keys[uuid(99)] = policy_public
    delivery = signed.signed_delivery(with_lease=True)
    await verifier.verify(delivery)
    await _assert_denied(verifier, delivery.model_copy(update={"signing_key_id": uuid(99)}))


async def test_policy_key_cannot_implicitly_validate_a_nested_lease(signed: SigningFixture) -> None:
    lease = signed.sign_lease(lease_claims())
    lease = lease.model_copy(
        update={
            "signing_key_id": uuid(17),
            "signature": signed.policy_signature(offline_auth_lease_claims_bytes(lease.claims)),
        }
    )
    delivery = signed.sign_claims(claims(lease=lease))
    await _assert_denied(signed.verifier, delivery)


@pytest.mark.parametrize("part", ["signature", "claims-rehashed", "hash", "unknown-key"])
async def test_valid_outer_signature_does_not_hide_nested_lease_tampering(
    signed: SigningFixture, part: str
) -> None:
    lease = signed.sign_lease(lease_claims())
    if part == "signature":
        lease = lease.model_copy(update={"signature": "ff" * 64})
    elif part == "claims-rehashed":
        changed = lease.claims.model_copy(update={"subject_revision": 14})
        lease = lease.model_copy(
            update={"claims": changed, "claims_hash": offline_auth_lease_claims_hash(changed)}
        )
    elif part == "hash":
        lease = lease.model_copy(update={"claims_hash": "ab" * 32})
    else:
        lease = lease.model_copy(update={"signing_key_id": uuid(99)})
    delivery = signed.sign_claims(claims(lease=lease))
    await _assert_denied(signed.verifier, delivery)


@pytest.mark.parametrize("restriction", ["readonly", "pos", "inventory", "archived", "suspended"])
async def test_valid_signatures_cannot_deliver_lease_exceeding_policy(
    signed: SigningFixture, restriction: str
) -> None:
    delivered_policy = policy()
    if restriction == "readonly":
        delivered_policy = policy(mode="readonly")
    elif restriction == "pos":
        delivered_policy = policy(blocked_modules=("pos",))
    elif restriction == "inventory":
        delivered_policy = policy(blocked_modules=("inventory",))
    elif restriction == "archived":
        delivered_policy = policy(status="archived")
    else:
        delivered_policy = policy(status="suspended")
    value = claims(lease=signed.sign_lease(lease_claims())).model_copy(
        update={"policy": delivered_policy}
    )
    await _assert_denied(signed.verifier, signed.sign_claims(value))


@pytest.mark.parametrize("offset", [0, -1])
async def test_lease_expiry_is_exclusive_at_delivery_time(
    signed: SigningFixture, offset: int
) -> None:
    expired = lease_claims().model_copy(
        update={"issued_at": NOW - timedelta(days=1), "expires_at": NOW + timedelta(seconds=offset)}
    )
    delivery = signed.sign_claims(claims(lease=signed.sign_lease(expired)))
    await _assert_denied(signed.verifier, delivery)


@pytest.mark.parametrize(
    "restriction", ["readonly", "suspended", "archived", "readonly-mode", "pos", "inventory"]
)
async def test_restrictive_policy_without_lease_is_deliverable(
    signed: SigningFixture, restriction: str
) -> None:
    if restriction == "readonly-mode":
        delivered_policy = policy(mode="readonly")
    elif restriction == "pos":
        delivered_policy = policy(blocked_modules=("pos",))
    elif restriction == "inventory":
        delivered_policy = policy(blocked_modules=("inventory",))
    else:
        delivered_policy = policy().model_copy(update={"tenant_status": restriction})
    value = claims().model_copy(update={"policy": delivered_policy})
    delivery = signed.sign_claims(value)
    assert delivery.claims.lease is None
    await signed.verifier.verify(delivery)


@pytest.mark.parametrize("nested", ["claims", "scope", "policy", "lease", "envelope"])
async def test_corrupted_models_fail_generically_without_serializer_warnings(
    signed: SigningFixture, nested: str
) -> None:
    delivery = signed.signed_delivery(with_lease=True)
    value = delivery.claims
    if nested == "claims":
        value = value.model_copy(update={"delivery_sequence": "sensitive-placeholder"})
    elif nested == "scope":
        value = value.model_copy(
            update={
                "scope": value.scope.model_copy(update={"writer_epoch": "sensitive-placeholder"})
            }
        )
    elif nested == "policy":
        value = value.model_copy(
            update={"policy": value.policy.model_copy(update={"version": "sensitive-placeholder"})}
        )
    elif nested == "lease":
        assert value.lease is not None
        changed = value.lease.claims.model_copy(
            update={"subject_revision": "sensitive-placeholder"}
        )
        value = value.model_copy(
            update={"lease": value.lease.model_copy(update={"claims": changed})}
        )
    else:
        delivery = delivery.model_copy(update={"signature": b"sensitive-placeholder"})
    await _assert_denied(signed.verifier, delivery.model_copy(update={"claims": value}))
