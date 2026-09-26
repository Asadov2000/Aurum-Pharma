"""Real lease v1 signature verification; no issuer or runtime admission wiring."""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from types import MappingProxyType
from uuid import UUID

from Crypto.PublicKey.ECC import EccKey
from Crypto.Signature import eddsa

from app.domains.sync.offline_auth import (
    OFFLINE_AUTH_UNAVAILABLE_MESSAGE,
    OfflineAuthUnavailableError,
)
from app.domains.sync.offline_auth_lease import (
    assert_offline_auth_lease_claims_hash,
    offline_auth_lease_claims_bytes,
)
from app.domains.sync.schemas import SignedOfflineAuthLeaseV1

_ED25519_ORDER = 2**252 + 27742317777372353535851937790883648493
_KEY_CONFIGURATION_ERROR = "Trusted offline signing keys are invalid"


def _canonical_public_key(encoded: bytes) -> EccKey:
    if type(encoded) is not bytes or len(encoded) != 32:
        raise ValueError(_KEY_CONFIGURATION_ERROR)
    key = eddsa.import_public_key(encoded)
    # Import alone is insufficient: reject noncanonical encodings, including an
    # identity encoding whose sign bit would be silently normalized on import.
    exported = key.export_key(format="raw")
    if not isinstance(exported, bytes) or not hmac.compare_digest(exported, encoded):
        raise ValueError(_KEY_CONFIGURATION_ERROR)
    return key


def _verify_ed25519(public_key: bytes, message: bytes, signature: bytes) -> None:
    if type(message) is not bytes or type(signature) is not bytes or len(signature) != 64:
        raise ValueError("Invalid offline signature")
    # RFC 8032 requires S < L. Enforce the strict boundary before invoking the
    # locked library; never accept a malleable S == L or noncanonical R.
    if int.from_bytes(signature[32:], "little") >= _ED25519_ORDER:
        raise ValueError("Invalid offline signature")
    _canonical_public_key(signature[:32])
    # PureEdDSA: full domain-prefixed canonical bytes, not a hash object, digest,
    # context variant or a caller-selected algorithm.
    eddsa.new(_canonical_public_key(public_key), "rfc8032").verify(message, signature)


class Ed25519OfflineLeaseSignatureVerifier:
    """Verify with an explicitly trusted, immutable snapshot of Cloud public keys.

    Keys must be supplied by a trusted composition owner, never a lease, tenant
    request, URL or auto-discovery path. Replacing this snapshot is a separate
    trusted rotation step; verification alone proves neither expiry nor admission.
    """

    def __init__(self, trusted_keys: Mapping[UUID, bytes]) -> None:
        try:
            copied: dict[UUID, bytes] = {}
            material: set[bytes] = set()
            for key_id, encoded in trusted_keys.items():
                if not isinstance(key_id, UUID) or key_id.version != 4:
                    raise ValueError(_KEY_CONFIGURATION_ERROR)
                key = _canonical_public_key(encoded)
                if (key.pointQ * 8).xy == (0, 1) or encoded in material:
                    raise ValueError(_KEY_CONFIGURATION_ERROR)
                # Duplicate material under different IDs would let unsigned
                # envelope key_id aliases conceal which trusted key was used.
                material.add(encoded)
                copied[key_id] = encoded
            self._keys: Mapping[UUID, bytes] = MappingProxyType(copied)
        except Exception:
            raise ValueError(_KEY_CONFIGURATION_ERROR) from None

    async def verify(self, lease: SignedOfflineAuthLeaseV1, signing_bytes: bytes) -> None:
        try:
            validated = SignedOfflineAuthLeaseV1.model_validate(
                lease.model_dump(mode="python", warnings="error")
            )
            expected = offline_auth_lease_claims_bytes(validated.claims)
            if type(signing_bytes) is not bytes or not hmac.compare_digest(signing_bytes, expected):
                raise ValueError("Invalid offline signing bytes")
            assert_offline_auth_lease_claims_hash(validated)
            public_key = self._keys[validated.signing_key_id]
            _verify_ed25519(public_key, expected, bytes.fromhex(validated.signature))
        except Exception:
            raise OfflineAuthUnavailableError(OFFLINE_AUTH_UNAVAILABLE_MESSAGE) from None
