"""Edge-only credential primitives; neither login admission nor protected storage.

The device pepper must come from the trusted Windows protector. It is never
stored with the verifier. These functions deliberately have no HTTP/runtime
composition and cannot substitute for owner admission or durable attempt limits.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from typing import Literal, Self

from passlib.context import CryptContext
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator, model_validator

from app.domains.sync.integrity import canonical_json_bytes
from app.domains.sync.schemas import (
    OfflineAuthDeviceBindingV0,
    OfflineAuthLeaseReferenceV1,
    OfflineAuthScopeV0,
)

_CONTEXT_DOMAIN = b"aurum:edge-local-credential-context:v1\x00"
_PASSWORD_DOMAIN = b"aurum:edge-local-credential-password:v1\x00"
_SECRET_PREFIX = "aurum-local-v1."
_SECRET_PATTERN = re.compile(r"aurum-local-v1\.[A-Za-z0-9_-]{32}\Z")
_VERIFIER_PATTERN = re.compile(r"\$2b\$12\$[./A-Za-z0-9]{53}\Z")
_PASSWORD_CONTEXT = CryptContext(schemes=["bcrypt"], bcrypt__rounds=12)
_UNAVAILABLE = "Offline local credentials are unavailable"


class LocalCredentialUnavailableError(RuntimeError):
    """No secret, verifier, adapter diagnostic or Cloud identity is disclosed."""


class LocalCredentialContextV1(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )

    schema_version: Literal[1]
    audience: Literal["aurum-edge-local-auth-v1"]
    scope: OfflineAuthScopeV0
    local_credential: OfflineAuthLeaseReferenceV1
    device_binding: OfflineAuthDeviceBindingV0
    protection_key: OfflineAuthDeviceBindingV0

    @model_validator(mode="after")
    def validate_key_separation(self) -> Self:
        if (
            self.device_binding.key_id == self.protection_key.key_id
            or self.device_binding.spki_sha256 == self.protection_key.spki_sha256
        ):
            raise ValueError("local secret protection requires a distinct device key")
        return self


class LocalCredentialVerifierV1(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )

    schema_version: Literal[1]
    algorithm: Literal["bcrypt-hmac-sha384-v1"]
    context: LocalCredentialContextV1
    verifier: SecretStr

    @field_validator("verifier")
    @classmethod
    def validate_verifier(cls, value: SecretStr) -> SecretStr:
        # Bound the work before asking bcrypt to interpret an untrusted cost.
        if _VERIFIER_PATTERN.fullmatch(value.get_secret_value()) is None:
            raise ValueError("unsupported local verifier format")
        return value


@dataclass(frozen=True, slots=True)
class IssuedLocalCredentialV1:
    record: LocalCredentialVerifierV1
    secret: SecretStr = field(repr=False)


def local_credential_context_digest(context: LocalCredentialContextV1) -> bytes:
    """Opaque binding shared with the protector, never authorization evidence."""
    validated = LocalCredentialContextV1.model_validate(context.model_dump(mode="python"))
    return hashlib.sha256(
        _CONTEXT_DOMAIN + canonical_json_bytes(validated.model_dump(mode="json"))
    ).digest()


def _prepared_password(secret: str, pepper: bytes, context_digest: bytes) -> str:
    if type(pepper) is not bytes or len(pepper) != 32:
        raise ValueError("local credential protector is unavailable")
    if not isinstance(secret, str) or _SECRET_PATTERN.fullmatch(secret) is None:
        raise ValueError("unsupported local secret format")
    # Fixed domain + fixed 32-byte digest + fixed-format secret is unambiguous.
    digest = hmac.new(
        pepper, _PASSWORD_DOMAIN + context_digest + secret.encode("ascii"), hashlib.sha384
    ).digest()
    # 48-byte SHA384 -> 64 ASCII bytes, no bcrypt NUL/truncation ambiguity.
    return base64.b64encode(digest).decode("ascii")


def issue_local_credential(
    context: LocalCredentialContextV1, *, pepper: bytes
) -> IssuedLocalCredentialV1:
    """Generate a separate 192-bit secret; no Cloud password/hash import exists."""
    try:
        validated = LocalCredentialContextV1.model_validate(context.model_dump(mode="python"))
        context_digest = local_credential_context_digest(validated)
        secret = _SECRET_PREFIX + secrets.token_urlsafe(24)
        verifier = _PASSWORD_CONTEXT.hash(_prepared_password(secret, pepper, context_digest))
        return IssuedLocalCredentialV1(
            record=LocalCredentialVerifierV1(
                schema_version=1,
                algorithm="bcrypt-hmac-sha384-v1",
                context=validated,
                verifier=SecretStr(verifier),
            ),
            secret=SecretStr(secret),
        )
    except Exception:
        raise LocalCredentialUnavailableError(_UNAVAILABLE) from None


def verify_local_credential(
    record: LocalCredentialVerifierV1,
    secret: str,
    *,
    expected_context: LocalCredentialContextV1,
    pepper: bytes,
) -> bool:
    """Crypto comparison only; caller must enforce trusted state and attempt limits."""
    try:
        validated = LocalCredentialVerifierV1.model_validate(record.model_dump(mode="python"))
        expected_digest = local_credential_context_digest(expected_context)
        if not hmac.compare_digest(
            local_credential_context_digest(validated.context), expected_digest
        ):
            return False
        prepared = _prepared_password(secret, pepper, expected_digest)
        return _PASSWORD_CONTEXT.verify(prepared, validated.verifier.get_secret_value())
    except Exception:
        return False
