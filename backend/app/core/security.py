"""JWT, password hashing, and the small primitives the auth domain needs.

bcrypt — for user passwords (slow on purpose, costs ~250ms — protects against
brute force, but too slow for short-lived codes).
sha256 — for email codes (with per-code salt) and refresh tokens (no salt;
the token itself is already 256 bits of entropy).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any
from uuid import UUID

import jwt
from jwt.exceptions import InvalidTokenError
from passlib.context import CryptContext

from app.core.config import get_settings
from app.core.errors import AuthenticationError
from app.core.time import utc_now

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


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
    """sha256(code + salt) — hex digest."""
    return hashlib.sha256(f"{code}{salt}".encode()).hexdigest()


def generate_refresh_token() -> str:
    """32 bytes random, hex-encoded (64 chars)."""
    return secrets.token_hex(32)


def hash_token(token: str) -> str:
    """sha256 of the refresh token — no salt needed, token is already 256-bit."""
    return hashlib.sha256(token.encode()).hexdigest()


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
) -> str:
    """Access token = short-lived JWT with identity claims only.

    No permissions list inside — permissions live in Redis cache, keyed by user
    and tenant. This keeps the header small and lets us invalidate immediately
    when an assignment changes.
    """
    extra: dict[str, Any] = {
        "tenant_id": str(tenant_id) if tenant_id else None,
        "is_developer": is_developer,
        "is_administrator": is_administrator,
    }
    return encode_token(
        str(user_id),
        expires_in=timedelta(minutes=settings.ACCESS_TOKEN_MINUTES),
        extra=extra,
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """Alias for decode_token kept for naming symmetry in the auth domain."""
    return decode_token(token)
