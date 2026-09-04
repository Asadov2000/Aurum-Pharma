"""Test-only helpers for authenticated platform support sessions."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    derive_mfa_encryption_key,
    generate_totp_secret,
    hash_token,
)
from app.core.time import utc_now
from app.domains.auth.models import AppUser, Session, SupportMfa


async def create_support_access_token(
    db_session: AsyncSession,
    user: AppUser,
    *,
    tenant_id: UUID | None = None,
    mfa_verified_at: datetime | None = None,
) -> str:
    """Create the same minimum state that a successful support MFA login owns."""
    if not (user.is_developer or user.is_administrator):
        raise ValueError("Support flags are required")

    verified_at = mfa_verified_at or utc_now()
    key_version = get_settings().MFA_ENCRYPTION_KEY_VERSION
    secret = generate_totp_secret()
    ciphertext = await db_session.scalar(
        text(
            "SELECT public.pgp_sym_encrypt("
            ":secret, :encryption_key, "
            "'cipher-algo=aes256, compress-algo=0')"
        ),
        {
            "secret": secret,
            "encryption_key": derive_mfa_encryption_key(),
        },
    )
    mfa = await db_session.get(SupportMfa, user.id)
    if mfa is None:
        mfa = SupportMfa(
            user_id=user.id,
            active_secret_ciphertext=ciphertext,
            active_key_version=key_version,
            status="active",
            active_generation=1,
            last_used_counter=None,
            confirmed_at=verified_at,
        )
        db_session.add(mfa)
    else:
        mfa.active_secret_ciphertext = ciphertext
        mfa.active_key_version = key_version
        mfa.pending_secret_ciphertext = None
        mfa.pending_key_version = None
        mfa.pending_generation = None
        mfa.status = "active"
        mfa.active_generation = max(mfa.active_generation or 0, 1)
        mfa.last_used_counter = None
        mfa.confirmed_at = verified_at

    session = Session(
        user_id=user.id,
        refresh_token_hash=hash_token(secrets.token_hex(32)),
        expires_at=verified_at + timedelta(days=1),
        mfa_verified_at=verified_at,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)
    return create_access_token(
        user.id,
        tenant_id=tenant_id,
        is_developer=user.is_developer,
        is_administrator=user.is_administrator,
        session_id=session.id,
        mfa_verified_at=verified_at,
    )


async def create_tenant_access_token(
    db_session: AsyncSession,
    user: AppUser,
    *,
    tenant_id: UUID | None = None,
    is_developer: bool | None = None,
    is_administrator: bool | None = None,
    mfa_verified_at: datetime | None = None,
) -> str:
    """Create a revocable tenant access token backed by a live auth session."""
    return await create_session_access_token(
        db_session,
        user_id=user.id,
        tenant_id=tenant_id if tenant_id is not None else user.home_tenant_id,
        is_developer=user.is_developer if is_developer is None else is_developer,
        is_administrator=(user.is_administrator if is_administrator is None else is_administrator),
        mfa_verified_at=mfa_verified_at,
    )


async def create_session_access_token(
    db_session: AsyncSession,
    *,
    user_id: UUID,
    tenant_id: UUID | None,
    is_developer: bool = False,
    is_administrator: bool = False,
    mfa_verified_at: datetime | None = None,
) -> str:
    """Create a revocable access token when a test only has the user id."""
    issued_at = utc_now()
    session = Session(
        user_id=user_id,
        refresh_token_hash=hash_token(secrets.token_hex(32)),
        expires_at=issued_at + timedelta(days=1),
        mfa_verified_at=mfa_verified_at,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)
    return create_access_token(
        user_id,
        tenant_id=tenant_id,
        is_developer=is_developer,
        is_administrator=is_administrator,
        session_id=session.id,
        mfa_verified_at=mfa_verified_at,
    )
