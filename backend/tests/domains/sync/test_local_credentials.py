"""Real Edge credential crypto with synthetic, independently trusted context."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import TypeVar, cast
from unittest.mock import Mock
from uuid import UUID

import pytest
from passlib.hash import bcrypt
from pydantic import BaseModel, SecretStr, TypeAdapter, ValidationError

from app.domains.sync import local_credentials as credentials
from app.domains.sync.local_credentials import (
    IssuedLocalCredentialV1,
    LocalCredentialContextV1,
    LocalCredentialUnavailableError,
    LocalCredentialVerifierV1,
    issue_local_credential,
    local_credential_context_digest,
    verify_local_credential,
)
from app.domains.sync.schemas import (
    OfflineAuthDeviceBindingV0,
    OfflineAuthLeaseReferenceV1,
    OfflineAuthScopeV0,
)

PEPPER = bytes(range(32))
Model = TypeVar("Model", bound=BaseModel)


def _uuid(value: int) -> UUID:
    return UUID(f"{value:08x}-0000-4000-8000-000000000001")


def _context() -> LocalCredentialContextV1:
    return LocalCredentialContextV1(
        schema_version=1,
        audience="aurum-edge-local-auth-v1",
        scope=OfflineAuthScopeV0(
            activation_id=_uuid(1),
            tenant_id=_uuid(2),
            branch_id=_uuid(3),
            edge_node_id=_uuid(4),
            register_id=_uuid(5),
            user_id=_uuid(6),
            writer_epoch=7,
            capability="cash_sale_v1",
        ),
        local_credential=OfflineAuthLeaseReferenceV1(reference_id=_uuid(8), revision=9),
        device_binding=OfflineAuthDeviceBindingV0(
            method="tpm2", key_id=_uuid(10), spki_sha256="ab" * 32
        ),
        protection_key=OfflineAuthDeviceBindingV0(
            method="tpm2", key_id=_uuid(11), spki_sha256="cd" * 32
        ),
    )


def _changed(model: Model, path: str, value: object) -> Model:
    key, separator, remaining = path.partition(".")
    if separator:
        nested = getattr(model, key)
        assert isinstance(nested, BaseModel)
        value = _changed(nested, remaining, value)
    return model.model_copy(update={key: value})


@pytest.fixture(scope="module")
def issued_pair() -> tuple[IssuedLocalCredentialV1, IssuedLocalCredentialV1]:
    # Reuse expensive immutable bcrypt fixtures; no real credentials or DB.
    return (
        issue_local_credential(_context(), pepper=PEPPER),
        issue_local_credential(_context(), pepper=PEPPER),
    )


def test_real_bcrypt_round_trip_and_fixed_secret_format(
    issued_pair: tuple[IssuedLocalCredentialV1, IssuedLocalCredentialV1],
) -> None:
    issued, _ = issued_pair
    secret = issued.secret.get_secret_value()
    verifier = issued.record.verifier.get_secret_value()
    assert isinstance(issued.secret, SecretStr)
    assert isinstance(issued.record.verifier, SecretStr)
    assert issued.record.algorithm == "bcrypt-hmac-sha384-v1"
    assert secret.startswith("aurum-local-v1.")
    suffix = secret.removeprefix("aurum-local-v1.")
    assert len(suffix) == 32
    assert len(base64.urlsafe_b64decode(suffix)) == 24
    assert len(verifier) == 60
    assert verifier.startswith("$2b$12$")
    assert verify_local_credential(
        issued.record, secret, expected_context=_context(), pepper=PEPPER
    )


def test_independent_issuance_uses_new_random_secret_and_bcrypt_salt(
    issued_pair: tuple[IssuedLocalCredentialV1, IssuedLocalCredentialV1],
) -> None:
    first, second = issued_pair
    assert first.secret != second.secret
    first_salt = first.record.verifier.get_secret_value().split("$")[3][:22]
    second_salt = second.record.verifier.get_secret_value().split("$")[3][:22]
    assert first_salt != second_salt
    assert first.record.context == second.record.context
    assert not verify_local_credential(
        first.record,
        second.secret.get_secret_value(),
        expected_context=_context(),
        pepper=PEPPER,
    )


def test_wrong_device_pepper_cannot_verify_a_valid_secret(
    issued_pair: tuple[IssuedLocalCredentialV1, IssuedLocalCredentialV1],
) -> None:
    issued, _ = issued_pair
    assert not verify_local_credential(
        issued.record,
        issued.secret.get_secret_value(),
        expected_context=_context(),
        pepper=bytes(reversed(PEPPER)),
    )


def test_verifier_matches_independent_domain_bound_hmac_sha384_preparation(
    issued_pair: tuple[IssuedLocalCredentialV1, IssuedLocalCredentialV1],
) -> None:
    issued, _ = issued_pair
    message = (
        b"aurum:edge-local-credential-password:v1\0"
        + local_credential_context_digest(_context())
        + issued.secret.get_secret_value().encode("ascii")
    )
    prepared = base64.b64encode(hmac.digest(PEPPER, message, "sha384"))
    assert len(prepared) == 64
    assert bcrypt.verify(prepared, issued.record.verifier.get_secret_value())


CONTEXT_CHANGES: list[tuple[str, object]] = [
    ("scope.activation_id", _uuid(99)),
    ("scope.tenant_id", _uuid(99)),
    ("scope.branch_id", _uuid(99)),
    ("scope.edge_node_id", _uuid(99)),
    ("scope.register_id", _uuid(99)),
    ("scope.user_id", _uuid(99)),
    ("scope.writer_epoch", 99),
    ("local_credential.reference_id", _uuid(99)),
    ("local_credential.revision", 99),
    ("device_binding.key_id", _uuid(99)),
    ("device_binding.spki_sha256", "ef" * 32),
    ("protection_key.key_id", _uuid(99)),
    ("protection_key.spki_sha256", "ef" * 32),
]


def test_context_digest_is_stable_canonical_and_domain_separated() -> None:
    context = _context()
    expected = hashlib.sha256(
        b"aurum:edge-local-credential-context:v1\0"
        + json.dumps(
            context.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).digest()
    reconstructed = LocalCredentialContextV1.model_validate(
        dict(reversed(list(dict(context).items())))
    )
    assert len(expected) == 32
    assert local_credential_context_digest(context) == expected
    assert local_credential_context_digest(reconstructed) == expected


@pytest.mark.parametrize(("path", "value"), CONTEXT_CHANGES)
def test_each_context_field_binds_the_digest_and_independent_verification(
    issued_pair: tuple[IssuedLocalCredentialV1, IssuedLocalCredentialV1],
    path: str,
    value: object,
) -> None:
    issued, _ = issued_pair
    other_context = _changed(_context(), path, value)
    assert local_credential_context_digest(other_context) != local_credential_context_digest(
        _context()
    )
    assert not verify_local_credential(
        issued.record,
        issued.secret.get_secret_value(),
        expected_context=other_context,
        pepper=PEPPER,
    )


@pytest.mark.parametrize(
    "path", ["scope.tenant_id", "local_credential.reference_id", "protection_key.key_id"]
)
def test_rebinding_record_context_cannot_reuse_the_old_verifier(
    issued_pair: tuple[IssuedLocalCredentialV1, IssuedLocalCredentialV1], path: str
) -> None:
    issued, _ = issued_pair
    rebound = _changed(_context(), path, _uuid(99))
    record = LocalCredentialVerifierV1.model_validate({**dict(issued.record), "context": rebound})
    assert not verify_local_credential(
        record, issued.secret.get_secret_value(), expected_context=rebound, pepper=PEPPER
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("schema_version", 2),
        ("audience", "aurum-cloud"),
        ("scope.capability", "cloud-login"),
        ("device_binding.method", "software"),
        ("protection_key.method", "software"),
        ("protection_key.key_id", _uuid(10)),
        ("protection_key.spki_sha256", "ab" * 32),
        ("local_credential.revision", True),
    ],
)
def test_invalid_context_and_nonseparated_keys_cannot_issue_or_verify(
    issued_pair: tuple[IssuedLocalCredentialV1, IssuedLocalCredentialV1],
    path: str,
    value: object,
) -> None:
    issued, _ = issued_pair
    invalid_context = _changed(_context(), path, value)
    with pytest.raises(ValidationError):
        local_credential_context_digest(invalid_context)
    with pytest.raises(LocalCredentialUnavailableError) as error:
        issue_local_credential(invalid_context, pepper=PEPPER)
    assert str(error.value) == "Offline local credentials are unavailable"
    assert error.value.__suppress_context__ is True
    assert not verify_local_credential(
        issued.record,
        issued.secret.get_secret_value(),
        expected_context=invalid_context,
        pepper=PEPPER,
    )


@pytest.mark.parametrize("construction", ["copy", "construct"])
@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("schema_version", 2),
        ("algorithm", "bcrypt"),
        ("context.audience", "aurum-cloud"),
        ("verifier", SecretStr("$2b$31$" + "a" * 53)),
        ("verifier", SecretStr("$2b$99$" + "a" * 53)),
        ("verifier", SecretStr("$2b$04$" + "a" * 53)),
        ("verifier", SecretStr("$2a$12$" + "a" * 53)),
        ("verifier", SecretStr("x" * 10_000)),
    ],
)
def test_untrusted_record_is_revalidated_before_expensive_bcrypt(
    issued_pair: tuple[IssuedLocalCredentialV1, IssuedLocalCredentialV1],
    monkeypatch: pytest.MonkeyPatch,
    construction: str,
    path: str,
    value: object,
) -> None:
    issued, _ = issued_pair
    invalid = _changed(issued.record, path, value)
    if construction == "construct":
        invalid = LocalCredentialVerifierV1.model_construct(**dict(invalid))
    expensive_verify = Mock(return_value=False)
    monkeypatch.setattr(credentials._PASSWORD_CONTEXT, "verify", expensive_verify)

    assert not verify_local_credential(
        invalid, issued.secret.get_secret_value(), expected_context=_context(), pepper=PEPPER
    )
    expensive_verify.assert_not_called()


@pytest.mark.parametrize("pepper", [b"", b"x" * 31, b"x" * 33, bytearray(32), "x" * 32, None])
def test_invalid_device_pepper_fails_without_leaking_details(
    issued_pair: tuple[IssuedLocalCredentialV1, IssuedLocalCredentialV1], pepper: object
) -> None:
    issued, _ = issued_pair
    with pytest.raises(LocalCredentialUnavailableError) as error:
        issue_local_credential(_context(), pepper=cast(bytes, pepper))
    assert str(error.value) == "Offline local credentials are unavailable"
    assert error.value.__suppress_context__ is True
    assert not verify_local_credential(
        issued.record,
        issued.secret.get_secret_value(),
        expected_context=_context(),
        pepper=cast(bytes, pepper),
    )


@pytest.mark.parametrize(
    "secret",
    [
        None,
        b"aurum-local-v1." + b"a" * 32,
        "",
        "password-from-cloud",
        "aurum-local-v1." + "a" * 31,
        "aurum-local-v1." + "a" * 33,
        "aurum-local-v1." + "a" * 32 + "\n",
        "aurum-local-v1." + "a" * 31 + "\0",
        "aurum-local-v1." + "я" * 32,
        "aurum-local-v1." + "a" * 10_000,
    ],
)
def test_malformed_secret_returns_false_without_exceptions(
    issued_pair: tuple[IssuedLocalCredentialV1, IssuedLocalCredentialV1], secret: object
) -> None:
    issued, _ = issued_pair
    assert not verify_local_credential(
        issued.record, cast(str, secret), expected_context=_context(), pepper=PEPPER
    )


def test_record_and_issued_value_never_expose_secrets_in_repr_or_json(
    issued_pair: tuple[IssuedLocalCredentialV1, IssuedLocalCredentialV1],
) -> None:
    issued, _ = issued_pair
    serialized = "\n".join(
        [
            repr(issued),
            repr(issued.record),
            repr(issued.secret),
            issued.record.model_dump_json(),
            TypeAdapter(IssuedLocalCredentialV1).dump_json(issued).decode("utf-8"),
        ]
    )
    assert issued.secret.get_secret_value() not in serialized
    assert issued.record.verifier.get_secret_value() not in serialized
    assert "**********" in serialized
    assert "pepper" not in LocalCredentialVerifierV1.model_fields
