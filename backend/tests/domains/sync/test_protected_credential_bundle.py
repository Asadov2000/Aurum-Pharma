"""Credential bundle parsing and bindings without SQL, TPM or real material."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from app.domains.sync.local_credentials import (
    LocalCredentialContextV1,
    local_credential_context_digest,
    verify_local_credential,
)
from app.domains.sync.protected_credential_bundle import (
    MAX_PROTECTED_CREDENTIAL_BUNDLE_BYTES,
    decode_protected_credential_bundle,
    encode_protected_credential_bundle,
    protected_credential_bundle_digest,
)

_FIXTURES = Path(__file__).resolve().parents[4] / "docs/fixtures"
_FIXTURE = _FIXTURES / "protected-credential-bundle-v1.json"
_DOMAIN = b"aurum:protected-credential-bundle:v1\x00"
_UNAVAILABLE = "Protected credential bundle is unavailable"


def _document() -> dict[str, object]:
    value: object = json.loads(_FIXTURE.read_bytes())
    assert isinstance(value, dict)
    return value


def _at(document: object, path: str) -> object:
    current = document
    for key in path.split("."):
        if isinstance(current, dict):
            current = current[key]
        else:
            assert isinstance(current, list)
            current = current[int(key)]
    return current


def _set(document: dict[str, object], path: str, value: object) -> None:
    prefix, _, key = path.rpartition(".")
    parent = document if not prefix else _at(document, prefix)
    assert isinstance(parent, dict)
    parent[key] = value


def _raw(document: dict[str, object]) -> bytes:
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


def test_shared_golden_digest_round_trip_and_unchanged_nested_snapshot() -> None:
    raw = _FIXTURE.read_bytes()
    bundle = decode_protected_credential_bundle(raw)
    expected = _FIXTURE.with_suffix(".sha256").read_text(encoding="ascii").strip()
    assert protected_credential_bundle_digest(raw).hex() == expected
    assert hashlib.sha256(_DOMAIN + raw).hexdigest() == expected
    assert encode_protected_credential_bundle(bundle) == raw
    assert bundle.cashier_state.model_dump(mode="json") == json.loads(
        (_FIXTURES / "protected-cashier-state-v1.json").read_bytes()
    )


def test_synthetic_verifier_integrates_with_existing_local_credential_crypto() -> None:
    # Deliberately public artificial test inputs; the envelope is synthetic and
    # does not encrypt this pepper or claim a real RSA/TPM provisioning result.
    bundle = decode_protected_credential_bundle(_FIXTURE.read_bytes())
    material = bundle.credential_materials[0]
    context = bundle.cashier_state.cashiers[0].credential_context
    synthetic_secret = "aurum-local-v1." + "A" * 32
    assert verify_local_credential(
        material.record, synthetic_secret, expected_context=context, pepper=bytes(range(32))
    )
    assert not verify_local_credential(
        material.record, synthetic_secret, expected_context=context, pepper=b"\xff" * 32
    )
    assert not verify_local_credential(
        material.record,
        "aurum-local-v1." + "B" * 32,
        expected_context=context,
        pepper=bytes(range(32)),
    )


def test_model_repr_and_general_json_dump_do_not_expose_sensitive_material() -> None:
    bundle = decode_protected_credential_bundle(_FIXTURE.read_bytes())
    material = bundle.credential_materials[0]
    verifier = material.record.verifier.get_secret_value()
    ciphertext = material.pepper_envelope.ciphertext_base64
    for value in (bundle, material, material.record, material.pepper_envelope):
        assert verifier not in repr(value)
        assert ciphertext not in repr(value)
    exported = bundle.model_dump_json()
    assert verifier not in exported
    assert "**********" in exported
    with pytest.raises(ValueError, match=_UNAVAILABLE):
        decode_protected_credential_bundle(exported.encode())
    assert verifier.encode() in encode_protected_credential_bundle(bundle)


def test_validation_errors_hide_invalid_verifier_and_ciphertext() -> None:
    wire = _document()
    verifier_marker = "private-invalid-verifier-marker"
    ciphertext_marker = "private-invalid-ciphertext-marker"
    _set(wire, "credential_materials.0.record.verifier", verifier_marker)
    _set(wire, "credential_materials.0.pepper_envelope.ciphertext_base64", ciphertext_marker)
    with pytest.raises(ValueError) as error:
        decode_protected_credential_bundle(_raw(wire))
    assert str(error.value) == _UNAVAILABLE
    assert verifier_marker not in repr(error.value)
    assert ciphertext_marker not in repr(error.value)


def test_hash_binds_exact_outer_bytes_without_inner_digest_substitution() -> None:
    raw = _FIXTURE.read_bytes()
    spaced = b"\n" + raw + b" "
    escaped = raw.replace(b"bcrypt", b"bcry\\u0070t")
    bundle = decode_protected_credential_bundle(raw)
    assert decode_protected_credential_bundle(spaced) == bundle
    assert decode_protected_credential_bundle(escaped) == bundle
    assert len({protected_credential_bundle_digest(item) for item in (raw, spaced, escaped)}) == 3
    assert (
        protected_credential_bundle_digest(raw)
        != hashlib.sha256(
            b"aurum:protected-cashier-state:v1\x00"
            + (_FIXTURES / "protected-cashier-state-v1.json").read_bytes()
        ).digest()
    )
    wire = _document()
    _set(
        wire,
        "credential_materials.0.pepper_envelope.ciphertext_base64",
        base64.b64encode(b"\xff" * 256).decode("ascii"),
    )
    assert protected_credential_bundle_digest(_raw(wire)) != protected_credential_bundle_digest(raw)


@pytest.mark.parametrize("size", [256, 264, 504, 512])
def test_canonical_ciphertext_length_boundaries(size: int) -> None:
    wire = _document()
    _set(
        wire,
        "credential_materials.0.pepper_envelope.ciphertext_base64",
        base64.b64encode(b"\xff" * size).decode("ascii"),
    )
    bundle = decode_protected_credential_bundle(_raw(wire))
    assert (
        len(base64.b64decode(bundle.credential_materials[0].pepper_envelope.ciphertext_base64))
        == size
    )


@pytest.mark.parametrize(
    "ciphertext",
    [
        "",
        base64.b64encode(b"\xff" * 248).decode("ascii"),
        base64.b64encode(b"\xff" * 257).decode("ascii"),
        base64.b64encode(b"\xff" * 520).decode("ascii"),
        base64.b64encode(b"\xff" * 256).decode("ascii").rstrip("="),
        base64.b64encode(b"\xff" * 256).decode("ascii") + "=",
        base64.b64encode(b"\xff" * 256).decode("ascii") + "\n",
        base64.urlsafe_b64encode(b"\xff" * 256).decode("ascii"),
        base64.b64encode(b"\xff" * 256).decode("ascii")[:-3] + "x==",
        "\u00e9" * 344,
    ],
)
def test_rejects_noncanonical_base64_and_invalid_ciphertext_size(ciphertext: str) -> None:
    wire = _document()
    _set(wire, "credential_materials.0.pepper_envelope.ciphertext_base64", ciphertext)
    with pytest.raises(ValueError, match=_UNAVAILABLE):
        decode_protected_credential_bundle(_raw(wire))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("schema_version", True),
        ("schema_version", 2),
        ("credential_materials.0.user_id", "000000ff-0000-4000-8000-000000000001"),
        ("credential_materials.0.user_id", "00000007000040008000000000000001"),
        ("credential_materials.0.user_id", "00000007-0000-1000-8000-000000000001"),
        ("credential_materials.0.record.schema_version", True),
        ("credential_materials.0.record.algorithm", "bcrypt"),
        ("credential_materials.0.record.verifier", "**********"),
        ("credential_materials.0.record.verifier", "$2b$04$" + "." * 53),
        ("credential_materials.0.record.verifier", "$2b$12$" + "." * 54),
        ("credential_materials.0.record.context.local_credential.revision", 4),
        ("credential_materials.0.record.context.protection_key.spki_sha256", "00" * 32),
        ("credential_materials.0.record.context.schema_version", False),
        ("credential_materials.0.pepper_envelope.schema_version", True),
        ("credential_materials.0.pepper_envelope.schema_version", 2),
        (
            "credential_materials.0.pepper_envelope.protection_key_id",
            "000000ff-0000-4000-8000-000000000001",
        ),
        (
            "credential_materials.0.pepper_envelope.protection_key_id",
            "0000000b000040008000000000000001",
        ),
        ("credential_materials.0.pepper_envelope.expected_spki_sha256", "CD" * 32),
        ("credential_materials.0.pepper_envelope.expected_spki_sha256", "00" * 32),
        ("cashier_state.cashiers.0.guard.last_counter", 103),
        ("cashier_state.observed_time.utc_now", "2026-11-03T09:00:02.0000001Z"),
        ("cashier_state.workplace_session.confirmation.confirmed_at", 1_793_696_402),
    ],
)
def test_rejects_contract_and_cross_binding_changes(path: str, value: object) -> None:
    wire = _document()
    _set(wire, path, value)
    with pytest.raises(ValueError, match=_UNAVAILABLE):
        decode_protected_credential_bundle(_raw(wire))


@pytest.mark.parametrize(
    "path",
    [
        "",
        "cashier_state",
        "credential_materials.0",
        "credential_materials.0.record",
        "credential_materials.0.record.context",
        "credential_materials.0.pepper_envelope",
    ],
)
def test_rejects_unknown_members_including_plaintext_pepper(path: str) -> None:
    wire = _document()
    _set(wire, f"{path}.pepper" if path else "pepper", "artificial-input-marker")
    with pytest.raises(ValueError, match=_UNAVAILABLE):
        decode_protected_credential_bundle(_raw(wire))


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b'{"schema_version":1,"schema_version":1}',
        b'{"nested":{"verifier":1,"verifier":2}}',
        b'{"schema_version":1.0}',
        b'{"schema_version":1e0}',
        b'{"schema_version":NaN}',
        b'{"schema_version":Infinity}',
        b'{"schema_version":-Infinity}',
        b'{"value":"\\ud800"}',
        b'{"\\udfff":1}',
        b"\xff",
        b"\xef\xbb\xbf{}",
        b"[]",
        b"null",
        b"[" * 33 + b"0" + b"]" * 33,
    ],
    ids=lambda raw: f"bytes-{len(raw)}",
)
def test_rejects_malformed_ambiguous_or_unbounded_json(raw: bytes) -> None:
    with pytest.raises(ValueError, match=_UNAVAILABLE):
        decode_protected_credential_bundle(raw)
    with pytest.raises(ValueError, match=_UNAVAILABLE):
        protected_credential_bundle_digest(raw)


def test_rejects_oversize_payload_without_expanding_test_node_ids() -> None:
    raw = b" " * (MAX_PROTECTED_CREDENTIAL_BUNDLE_BYTES + 1)
    with pytest.raises(ValueError, match=_UNAVAILABLE):
        decode_protected_credential_bundle(raw)


def test_one_to_one_materials_required_even_for_revoked_cashiers() -> None:
    wire = _document()
    _set(wire, "cashier_state.cashiers.0.authorization.status", "revoked")
    assert len(decode_protected_credential_bundle(_raw(wire)).credential_materials) == 1
    wire["credential_materials"] = []
    with pytest.raises(ValueError, match=_UNAVAILABLE):
        decode_protected_credential_bundle(_raw(wire))
    _set(wire, "cashier_state.cashiers", [])
    _set(wire, "cashier_state.workplace_session", None)
    assert decode_protected_credential_bundle(_raw(wire)).credential_materials == ()


def test_rejects_duplicate_extra_and_unsorted_materials() -> None:
    wire = _document()
    first = _at(wire, "credential_materials.0")
    wire["credential_materials"] = [first, first]
    with pytest.raises(ValueError, match=_UNAVAILABLE):
        decode_protected_credential_bundle(_raw(wire))
    wire["credential_materials"] = [first] * 257
    with pytest.raises(ValueError, match=_UNAVAILABLE):
        decode_protected_credential_bundle(_raw(wire))
    second_cashier: object = json.loads(json.dumps(_at(wire, "cashier_state.cashiers.0")))
    second_material: object = json.loads(json.dumps(first))
    assert isinstance(second_cashier, dict)
    assert isinstance(second_material, dict)
    user = "00000008-0000-4000-8000-000000000001"
    for path in (
        "authorization.scope.user_id",
        "credential_context.scope.user_id",
        "guard.user_id",
    ):
        _set(second_cashier, path, user)
    _set(second_material, "user_id", user)
    _set(second_material, "record.context.scope.user_id", user)
    context = LocalCredentialContextV1.model_validate_json(
        json.dumps(_at(second_cashier, "credential_context"))
    )
    _set(second_cashier, "guard.context_digest", local_credential_context_digest(context).hex())
    _set(wire, "cashier_state.cashiers", [_at(wire, "cashier_state.cashiers.0"), second_cashier])
    wire["credential_materials"] = [second_material, first]
    with pytest.raises(ValueError, match=_UNAVAILABLE):
        decode_protected_credential_bundle(_raw(wire))
    wire["credential_materials"] = [first, second_material]
    assert len(decode_protected_credential_bundle(_raw(wire)).credential_materials) == 2


def test_uppercase_uuid_parity_and_numeric_negative_zero() -> None:
    raw = _FIXTURE.read_bytes().replace(b"0000000b-0000-4000", b"0000000B-0000-4000")
    assert decode_protected_credential_bundle(raw) == decode_protected_credential_bundle(
        _FIXTURE.read_bytes()
    )
    wire = _document()
    _set(wire, "cashier_state.cashiers.0.guard.failed_attempts", 0)
    raw = _raw(wire).replace(b'"failed_attempts": 0', b'"failed_attempts": -0')
    assert (
        decode_protected_credential_bundle(raw).cashier_state.cashiers[0].guard.failed_attempts == 0
    )
