"""Bounded credential wire data; parsing never decrypts or grants admission.

The independently authenticated Windows checkpoint must bind the complete bundle,
including the exact verifier and encrypted pepper. Its nested cashier snapshot is
metadata, not a second trust root. Generic failures and repr hide material.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Literal, Self

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domains.sync.integrity import canonical_json_bytes
from app.domains.sync.local_credentials import LocalCredentialVerifierV1
from app.domains.sync.protected_cashier_state import (
    ProtectedCashierStateV1,
    decode_protected_cashier_state,
)

MAX_PROTECTED_CREDENTIAL_BUNDLE_BYTES = 1_048_576
_MAX_DEPTH = 32
_DOMAIN = b"aurum:protected-credential-bundle:v1\x00"
_UNAVAILABLE = "Protected credential bundle is unavailable"
_UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}" r"-[0-9a-fA-F]{12}\Z"
)


class _CredentialBundleModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        validate_default=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )

    @field_validator("schema_version", mode="before", check_fields=False)
    @classmethod
    def validate_version_type(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError(_UNAVAILABLE)
        return value


class ProtectedPepperEnvelopeV1(_CredentialBundleModel):
    schema_version: Literal[1]
    protection_key_id: UUID4
    expected_spki_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ciphertext_base64: str = Field(repr=False, min_length=344, max_length=684)

    @field_validator("ciphertext_base64")
    @classmethod
    def validate_ciphertext(cls, value: str) -> str:
        decoded = base64.b64decode(value, validate=True)
        if (
            not 256 <= len(decoded) <= 512
            or len(decoded) % 8 != 0
            or base64.b64encode(decoded).decode("ascii") != value
        ):
            raise ValueError(_UNAVAILABLE)
        return value


class ProtectedCredentialMaterialV1(_CredentialBundleModel):
    user_id: UUID4
    record: LocalCredentialVerifierV1 = Field(repr=False)
    pepper_envelope: ProtectedPepperEnvelopeV1 = Field(repr=False)


class ProtectedCredentialBundleV1(_CredentialBundleModel):
    schema_version: Literal[1]
    cashier_state: ProtectedCashierStateV1
    credential_materials: tuple[ProtectedCredentialMaterialV1, ...] = Field(
        repr=False, max_length=256
    )

    @model_validator(mode="after")
    def validate_material_bindings(self) -> Self:
        cashiers = self.cashier_state.cashiers
        users = tuple(item.user_id.hex for item in self.credential_materials)
        expected = tuple(item.authorization.scope.user_id.hex for item in cashiers)
        if users != expected:
            raise ValueError(_UNAVAILABLE)
        for material, cashier in zip(self.credential_materials, cashiers, strict=True):
            context = cashier.credential_context
            if (
                material.record.context != context
                or material.pepper_envelope.protection_key_id != context.protection_key.key_id
                or material.pepper_envelope.expected_spki_sha256
                != context.protection_key.spki_sha256
            ):
                raise ValueError(_UNAVAILABLE)
        return self


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(_UNAVAILABLE)
        result[key] = value
    return result


def _reject_non_integer(value: str) -> object:
    raise ValueError(_UNAVAILABLE)


def _validate_wire(value: object, *, depth: int = 1) -> None:
    if isinstance(value, dict | list) and depth > _MAX_DEPTH:
        raise ValueError(_UNAVAILABLE)
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
    elif isinstance(value, dict):
        for key, member in value.items():
            if not isinstance(key, str):
                raise ValueError(_UNAVAILABLE)
            key.encode("utf-8", errors="strict")
            if (
                key.endswith("_id")
                and key != "continuity_id"
                and (not isinstance(member, str) or _UUID_PATTERN.fullmatch(member) is None)
            ):
                raise ValueError(_UNAVAILABLE)
            _validate_wire(member, depth=depth + 1)
    elif isinstance(value, list):
        for member in value:
            _validate_wire(member, depth=depth + 1)
    elif value is not None and type(value) is not int:
        raise ValueError(_UNAVAILABLE)


def decode_protected_credential_bundle(raw: bytes) -> ProtectedCredentialBundleV1:
    """Validate bound wire data without signature, TPM, pepper or time authority."""
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_PROTECTED_CREDENTIAL_BUNDLE_BYTES:
            raise ValueError(_UNAVAILABLE)
        parsed: object = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_float=_reject_non_integer,
            parse_constant=_reject_non_integer,
        )
        _validate_wire(parsed)
        if not isinstance(parsed, dict) or "cashier_state" not in parsed:
            raise ValueError(_UNAVAILABLE)
        # Reuse the original snapshot's full wire and cross-record checks. Its
        # canonical re-encoding is for validation only; the outer hash stays raw.
        decode_protected_cashier_state(canonical_json_bytes(parsed["cashier_state"]))
        return ProtectedCredentialBundleV1.model_validate_json(raw)
    except (ValueError, RecursionError, UnicodeError):
        raise ValueError(_UNAVAILABLE) from None


def encode_protected_credential_bundle(bundle: ProtectedCredentialBundleV1) -> bytes:
    """Explicit sensitive export; general model JSON dumps keep verifiers masked."""
    try:
        wire = bundle.model_dump(mode="json", exclude={"credential_materials"})
        materials: list[dict[str, object]] = []
        for material in bundle.credential_materials:
            record = material.record.model_dump(mode="json", exclude={"verifier"})
            record["verifier"] = material.record.verifier.get_secret_value()
            materials.append(
                {
                    "user_id": str(material.user_id),
                    "record": record,
                    "pepper_envelope": material.pepper_envelope.model_dump(mode="json"),
                }
            )
        wire["credential_materials"] = materials
        raw = canonical_json_bytes(wire)
        decode_protected_credential_bundle(raw)
        return raw
    except (ValueError, RecursionError, UnicodeError):
        raise ValueError(_UNAVAILABLE) from None


def protected_credential_bundle_digest(raw: bytes) -> bytes:
    """Digest the complete exact valid payload, including encrypted material."""
    decode_protected_credential_bundle(raw)
    return hashlib.sha256(_DOMAIN + raw).digest()
