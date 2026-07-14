"""One-time Edge credentials. Only their domain-separated hash is persisted."""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from uuid import UUID, uuid4

_PREFIX = "edge_v1"
_SECRET_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_HASH_DOMAIN = b"aurum-edge-credential-v1\x00"


@dataclass(frozen=True, slots=True)
class EdgeCredential:
    kid: UUID
    secret: str

    @property
    def token(self) -> str:
        return f"{_PREFIX}.{self.kid}.{self.secret}"

    @property
    def digest(self) -> str:
        return credential_digest(self.kid, self.secret)


def issue_edge_credential() -> EdgeCredential:
    return EdgeCredential(kid=uuid4(), secret=secrets.token_hex(32))


def parse_edge_credential(token: str) -> EdgeCredential:
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != _PREFIX:
        raise ValueError("Invalid Edge credential format")
    try:
        kid = UUID(parts[1])
    except ValueError as exc:
        raise ValueError("Invalid Edge credential identifier") from exc
    secret = parts[2]
    if _SECRET_PATTERN.fullmatch(secret) is None:
        raise ValueError("Invalid Edge credential secret")
    return EdgeCredential(kid=kid, secret=secret)


def credential_digest(kid: UUID, secret: str) -> str:
    if _SECRET_PATTERN.fullmatch(secret) is None:
        raise ValueError("Invalid Edge credential secret")
    material = _HASH_DOMAIN + kid.bytes + bytes.fromhex(secret)
    return hashlib.sha256(material).hexdigest()
