"""A successful login from an unknown browser creates a mandatory warning."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_code_salt, hash_code, hash_token
from app.core.time import utc_now
from app.domains.audit.models import AuditLog
from app.domains.auth.models import EmailCode, Session
from app.domains.auth.repository import AuthRepository
from app.domains.auth.service import AuthService, AuthTokens, MfaLoginChallenge
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.notifications.models import Notification
from app.domains.roles.models import TenantMembership

DEVICE_A = "a" * 64
DEVICE_B = "b" * 64


async def _seed_code(db: AsyncSession, email: str, code: str = "123456") -> None:
    salt = generate_code_salt()
    db.add(
        EmailCode(
            email_lower=email.lower(),
            code_hash=hash_code(code, salt),
            code_salt=salt,
            purpose="login",
            ip_address="127.0.0.1",
            expires_at=utc_now() + timedelta(minutes=10),
        )
    )
    await db.flush()


async def _login(
    db: AsyncSession,
    service: AuthService,
    *,
    email: str,
    device_id: str,
) -> AuthTokens:
    await _seed_code(db, email)
    result = await service.verify_login_code(
        email=email,
        code="123456",
        password=None,
        ip_address="127.0.0.1",
        user_agent="Aurum security test",
        device_id=device_id,
    )
    assert not isinstance(result, MfaLoginChallenge)
    assert result.access_token
    return result


async def test_new_device_login_creates_one_redacted_mandatory_notification(
    db_session: AsyncSession,
    make_user,
) -> None:
    tenant = await FoundationService(FoundationRepository(db_session)).create_tenant(
        payload={
            "name": "Device security tenant",
            "contact_email": "device-security-tenant@aurum.tj",
        }
    )
    user = await make_user(
        email="device-security-user@aurum.tj",
        home_tenant_id=tenant.id,
    )
    db_session.add(
        TenantMembership(
            tenant_id=tenant.id,
            user_id=user.id,
            full_name=user.full_name,
            status="active",
        )
    )
    await db_session.flush()

    service = AuthService(AuthRepository(db_session))
    await _login(db_session, service, email=user.email, device_id=DEVICE_A)
    await _login(db_session, service, email=user.email, device_id=DEVICE_A)

    notifications_before = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == user.id,
                    Notification.event_type == "security.new_device_login",
                )
            )
        )
        .scalars()
        .all()
    )
    assert notifications_before == []

    await _login(db_session, service, email=user.email, device_id=DEVICE_B)
    await _login(db_session, service, email=user.email, device_id=DEVICE_B)

    notifications = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == user.id,
                    Notification.event_type == "security.new_device_login",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(notifications) == 1
    alert = notifications[0]
    assert alert.tenant_id == tenant.id
    assert alert.severity == "warning"
    assert alert.data == {"reason": "new_device", "action": "review_sessions"}
    assert DEVICE_A not in (alert.body or "")
    assert DEVICE_B not in (alert.body or "")
    assert "127.0.0.1" not in (alert.body or "")

    audit_events = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user.id,
                    AuditLog.table_name == "session",
                )
            )
        )
        .scalars()
        .all()
    )
    new_device_events = [
        event for event in audit_events if event.metadata_json == {"event": "new_device_login"}
    ]
    assert len(new_device_events) == 1

    sessions = (
        (await db_session.execute(select(Session).where(Session.user_id == user.id)))
        .scalars()
        .all()
    )
    assert [session.device_id_hash for session in sessions].count(hash_token(DEVICE_A)) == 2
    assert [session.device_id_hash for session in sessions].count(hash_token(DEVICE_B)) == 2
    assert all(session.device_id_hash not in {DEVICE_A, DEVICE_B} for session in sessions)


async def test_refresh_rotation_preserves_device_identity(
    db_session: AsyncSession,
    make_user,
) -> None:
    user = await make_user(email="device-rotation-user@aurum.tj")
    repo = AuthRepository(db_session)
    tokens = await _login(
        db_session,
        AuthService(repo),
        email=user.email,
        device_id=DEVICE_A,
    )

    rotated = await repo.rotate_session(
        old_token_hash=hash_token(tokens.refresh_token),
        new_token_hash=hash_token("rotated-device-refresh-token"),
        operation_id=uuid4(),
        user_agent="Aurum rotation test",
        ip_address="127.0.0.1",
        expires_at=utc_now() + timedelta(days=1),
    )

    assert rotated is not None
    rotated_session = await db_session.get(Session, rotated.id)
    assert rotated_session is not None
    assert rotated_session.device_id_hash == hash_token(DEVICE_A)
