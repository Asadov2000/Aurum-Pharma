"""Bootstrap delivery and proof matching preserve the invitation lifecycle."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_mailer_repository import AuthMailerRepository
from app.core.security import email_outbox_encryption_keyring_json, hash_code
from app.domains.auth.models import AppUser
from app.domains.auth.repository import AuthRepository
from app.domains.auth.service import AuthService
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService
from tests.domains.auth.test_identities import db_engine as db_engine  # noqa: PLC0414


async def _invited_account(db: AsyncSession) -> tuple[AppUser, UUID]:
    token = uuid4().hex
    tenant = await FoundationService(FoundationRepository(db)).create_tenant(
        payload={"name": f"Invitation {token}", "contact_email": f"tenant-{token}@example.com"}
    )
    user, membership = await RolesService(RolesRepository(db)).create_tenant_account(
        tenant_id=tenant.id, email=f"invited-{token}@example.com", full_name="Invited employee"
    )
    return user, membership.id


async def _invalidate_invitation(db: AsyncSession, membership_id: UUID, state: str) -> None:
    if state == "expired":
        change = "issued_at=now()-INTERVAL '8 days',expires_at=now()-INTERVAL '1 day'"
    else:
        change = "status='revoked',revoked_at=now()"
    await db.execute(
        text(f"UPDATE tenant_invitation SET {change} WHERE membership_id=:membership"),
        {"membership": membership_id},
    )


async def test_valid_invitation_is_bound_delivered_and_accepted(db_session: AsyncSession) -> None:
    user, membership_id = await _invited_account(db_session)
    service = AuthService(AuthRepository(db_session))
    code = await service.request_login_code(email=user.email, ip_address="127.0.0.1")
    assert (
        await db_session.scalar(
            text("""SELECT count(*) FROM auth_email_outbox delivery
        JOIN email_code code ON code.id=delivery.email_code_id
        JOIN tenant_invitation invitation ON invitation.id=code.tenant_invitation_id
        WHERE invitation.membership_id=:membership AND invitation.status='pending'"""),
            {"membership": membership_id},
        )
        == 1
    )
    claim = await AuthMailerRepository(db_session).claim_login_email(
        encryption_keyring=email_outbox_encryption_keyring_json(), lease_seconds=300
    )
    assert claim is not None
    assert claim.recipient_email == user.email
    assert claim.login_code == code
    await service.verify_login_code(
        email=user.email, code=claim.login_code, password=None, ip_address="127.0.0.1"
    )
    assert (
        await db_session.scalar(
            text("SELECT status FROM tenant_invitation WHERE membership_id=:membership"),
            {"membership": membership_id},
        )
        == "accepted"
    )


@pytest.mark.parametrize("state", ["orphan", "expired", "revoked"])
async def test_invalid_invitation_never_enqueues_delivery(
    db_session: AsyncSession, state: str
) -> None:
    if state == "orphan":
        user = AppUser(email=f"orphan-{uuid4().hex}@example.com", full_name="Orphan invitation")
        db_session.add(user)
        await db_session.flush()
    else:
        user, membership_id = await _invited_account(db_session)
        await _invalidate_invitation(db_session, membership_id, state)
    await AuthService(AuthRepository(db_session)).request_login_code(
        email=user.email, ip_address="127.0.0.1"
    )
    assert (
        await db_session.scalar(
            text("""SELECT count(*) FROM auth_email_outbox delivery
        JOIN email_code code ON code.id=delivery.email_code_id WHERE code.email_lower=:email"""),
            {"email": user.email},
        )
        == 0
    )


@pytest.mark.parametrize("state", ["expired", "revoked"])
async def test_issued_code_stops_matching_after_invitation_invalidated(
    db_session: AsyncSession, state: str
) -> None:
    user, membership_id = await _invited_account(db_session)
    repo = AuthRepository(db_session)
    code = await AuthService(repo).request_login_code(email=user.email, ip_address="127.0.0.1")
    challenge = await repo.find_active_email_code(user.email)
    assert challenge is not None
    candidate = hash_code(code, challenge.code_salt)
    assert await repo.email_code_matches(
        code_id=challenge.id, email_lower=user.email, candidate_hash=candidate
    )
    await _invalidate_invitation(db_session, membership_id, state)
    assert not await repo.email_code_matches(
        code_id=challenge.id, email_lower=user.email, candidate_hash=candidate
    )
    assert not await repo.confirm_bootstrap_email(challenge.id, candidate)
    assert (
        await db_session.scalar(
            text("SELECT email_verified_at FROM app_user WHERE id=:id"), {"id": user.id}
        )
        is None
    )
