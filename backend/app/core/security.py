"""JWT, password hashing, and the small primitives the auth domain needs.

bcrypt — for user passwords (slow on purpose, costs ~250ms — protects against
brute force, but too slow for short-lived codes).
HMAC-SHA256 — for email codes (server secret + per-code salt). Plain sha256 —
for refresh tokens (no salt; the token itself is already 256 bits of entropy).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode
from uuid import UUID

import jwt
from jwt.exceptions import InvalidTokenError
from passlib.context import CryptContext

from app.core.config import get_settings
from app.core.errors import AuthenticationError
from app.core.time import utc_now

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

TOTP_DIGITS = 6
TOTP_PERIOD_SECONDS = 30
TOTP_SECRET_BYTES = 20
RECOVERY_CODE_COUNT = 10


# -----------------------------------------------------------------------------
# Passwords (bcrypt)
# -----------------------------------------------------------------------------


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# -----------------------------------------------------------------------------
# Email codes and refresh tokens (sha256)
# -----------------------------------------------------------------------------


def generate_email_code() -> str:
    """6-digit numeric code, zero-padded. Uses secrets — never random.random()."""
    return f"{secrets.randbelow(1_000_000):06d}"


def generate_code_salt() -> str:
    """16 bytes hex (32 chars) per-code salt."""
    return secrets.token_hex(16)


def hash_code(code: str, salt: str) -> str:
    """Return a keyed verifier for a short-lived numeric code.

    Six decimal digits can be brute-forced after a database leak even with a
    per-row salt. The server secret keeps stored verifiers useless without the
    application configuration.
    """
    root_key = settings.JWT_SECRET.encode()
    code_key = hmac.new(root_key, b"aurum-email-code-key:v1", hashlib.sha256).digest()
    message = f"{code}:{salt}".encode()
    return hmac.new(code_key, message, hashlib.sha256).hexdigest()


def generate_refresh_token() -> str:
    """32 bytes random, hex-encoded (64 chars)."""
    return secrets.token_hex(32)


def generate_device_id() -> str:
    """Stable browser identifier; it is never an authentication credential."""
    return secrets.token_hex(32)


def derive_rotated_refresh_token(refresh_token: str, operation_id: UUID) -> str:
    """Derive the retry-stable successor for one refresh operation.

    The database still stores only a SHA-256 verifier. Repeating the same
    request can reproduce the plaintext successor after a lost response,
    while a different operation id produces a different candidate.
    """
    root_key = settings.JWT_SECRET.encode()
    rotation_key = hmac.new(
        root_key,
        b"aurum-refresh-rotation-key:v1",
        hashlib.sha256,
    ).digest()
    message = refresh_token.encode() + b"\x00" + operation_id.bytes
    return hmac.new(rotation_key, message, hashlib.sha256).hexdigest()


def hash_token(token: str) -> str:
    """sha256 of the refresh token — no salt needed, token is already 256-bit."""
    return hashlib.sha256(token.encode()).hexdigest()


# -----------------------------------------------------------------------------
# Support MFA (RFC 6238)
# -----------------------------------------------------------------------------


def _derive_key(label: bytes) -> bytes:
    return hmac.new(settings.JWT_SECRET.encode(), label, hashlib.sha256).digest()


def derive_mfa_encryption_key(
    *,
    version: int | None = None,
) -> str:
    """Derive a domain-separated key for pgcrypto.

    This value is passed only as a bound SQL parameter and must never be
    logged. Staging and production require independent root material; the JWT
    fallback exists only to keep local development data backward-compatible.
    """
    selected_version = version or settings.MFA_ENCRYPTION_KEY_VERSION
    if (
        selected_version == settings.MFA_ENCRYPTION_KEY_VERSION
        and settings.MFA_ENCRYPTION_KEY is not None
    ):
        root_key = settings.MFA_ENCRYPTION_KEY.get_secret_value().encode()
    elif selected_version in settings.MFA_ENCRYPTION_PREVIOUS_KEYS:
        root_key = (
            settings.MFA_ENCRYPTION_PREVIOUS_KEYS[selected_version].get_secret_value().encode()
        )
    elif (
        settings.ENVIRONMENT == "development"
        and selected_version == 1
        and settings.MFA_ENCRYPTION_KEY is None
    ):
        root_key = _derive_key(b"aurum-mfa-root:v1")
    else:
        raise ValueError(f"MFA encryption key version {selected_version} is unavailable")
    return hmac.new(
        root_key,
        f"aurum-totp-pgcrypto:v{selected_version}".encode(),
        hashlib.sha256,
    ).hexdigest()


def mfa_encryption_keyring_json() -> str:
    versions = set(settings.MFA_ENCRYPTION_PREVIOUS_KEYS)
    versions.add(settings.MFA_ENCRYPTION_KEY_VERSION)
    keyring = {
        str(version): derive_mfa_encryption_key(version=version) for version in sorted(versions)
    }
    return json.dumps(keyring, separators=(",", ":"), sort_keys=True)


def generate_totp_secret() -> str:
    """Generate a 160-bit Base32 secret compatible with authenticator apps."""
    return base64.b32encode(secrets.token_bytes(TOTP_SECRET_BYTES)).decode().rstrip("=")


def _decode_totp_secret(secret: str) -> bytes:
    normalized = secret.strip().replace(" ", "").upper()
    if not normalized or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for char in normalized):
        raise ValueError("Invalid TOTP secret")
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    try:
        decoded = base64.b32decode(normalized + padding, casefold=False)
    except ValueError as exc:
        raise ValueError("Invalid TOTP secret") from exc
    if len(decoded) < 16:
        raise ValueError("TOTP secret is too short")
    return decoded


def _totp_at_counter(secret: str, counter: int) -> str:
    if counter < 0:
        raise ValueError("TOTP counter cannot be negative")
    digest = hmac.new(
        _decode_totp_secret(secret),
        struct.pack(">Q", counter),
        hashlib.sha1,
    ).digest()
    offset = digest[-1] & 0x0F
    binary = (
        ((digest[offset] & 0x7F) << 24)
        | ((digest[offset + 1] & 0xFF) << 16)
        | ((digest[offset + 2] & 0xFF) << 8)
        | (digest[offset + 3] & 0xFF)
    )
    return f"{binary % (10**TOTP_DIGITS):0{TOTP_DIGITS}d}"


def match_totp_counter(
    secret: str,
    code: str,
    *,
    at: datetime | None = None,
    last_used_counter: int | None = None,
    window: int = 1,
) -> int | None:
    """Return a matching time counter while enforcing one-time use."""
    if len(code) != TOTP_DIGITS or not code.isascii() or not code.isdigit():
        return None
    if window < 0 or window > 2:
        raise ValueError("Unsupported TOTP window")

    current = int((at or utc_now()).timestamp()) // TOTP_PERIOD_SECONDS
    offsets = [0]
    for distance in range(1, window + 1):
        offsets.extend((-distance, distance))

    for offset in offsets:
        candidate = current + offset
        if candidate < 0:
            continue
        if last_used_counter is not None and candidate <= last_used_counter:
            continue
        if hmac.compare_digest(_totp_at_counter(secret, candidate), code):
            return candidate
    return None


def build_totp_uri(*, account_name: str, secret: str, issuer: str) -> str:
    """Build an otpauth URI without sending the secret to a third party."""
    label = quote(f"{issuer}:{account_name}", safe="")
    query = urlencode(
        {
            "secret": secret,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": str(TOTP_DIGITS),
            "period": str(TOTP_PERIOD_SECONDS),
        }
    )
    return f"otpauth://totp/{label}?{query}"


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    if count < 1 or count > 20:
        raise ValueError("Unsupported recovery-code count")
    codes: list[str] = []
    for _ in range(count):
        raw = base64.b32encode(secrets.token_bytes(12)).decode().rstrip("=")
        codes.append("-".join(raw[index : index + 5] for index in range(0, 20, 5)))
    return codes


def normalize_recovery_code(code: str) -> str:
    return "".join(char for char in code.upper() if char not in {"-", " "})


def hash_recovery_code(code: str) -> str:
    normalized = normalize_recovery_code(code)
    if len(normalized) != 20 or any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for char in normalized
    ):
        raise ValueError("Invalid recovery code")
    # Recovery codes contain 96 random bits, so a database dump cannot
    # feasibly brute-force this digest. Keeping the hash unkeyed also means
    # JWT or TOTP-encryption key rotation cannot invalidate saved codes.
    return hashlib.sha256(f"aurum-mfa-recovery-code:v2:{normalized}".encode()).hexdigest()


# -----------------------------------------------------------------------------
# JWT
# -----------------------------------------------------------------------------


def encode_token(
    subject: str,
    *,
    expires_in: timedelta,
    extra: dict[str, Any] | None = None,
) -> str:
    """Generic JWT factory used by the access-token helper below."""
    now = utc_now()
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_in).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Returns the JWT claims, or raises AuthenticationError on invalid / expired."""
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except InvalidTokenError as exc:
        raise AuthenticationError("Invalid or expired token") from exc
    return payload


def create_access_token(
    user_id: UUID,
    *,
    tenant_id: UUID | None,
    is_developer: bool,
    is_administrator: bool,
    session_id: UUID | None = None,
    mfa_verified_at: datetime | None = None,
) -> str:
    """Access token = short-lived JWT with identity claims only.

    No permissions list inside: authorization is loaded from PostgreSQL for the
    current user and tenant. This avoids stale JWT and cross-transaction cache
    state when an assignment changes.
    """
    extra: dict[str, Any] = {
        "tenant_id": str(tenant_id) if tenant_id else None,
        "is_developer": is_developer,
        "is_administrator": is_administrator,
        "sid": str(session_id) if session_id else None,
        "mfa_at": int(mfa_verified_at.timestamp()) if mfa_verified_at else None,
    }
    return encode_token(
        str(user_id),
        expires_in=timedelta(minutes=settings.ACCESS_TOKEN_MINUTES),
        extra=extra,
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """Alias for decode_token kept for naming symmetry in the auth domain."""
    return decode_token(token)
