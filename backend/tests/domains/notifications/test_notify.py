"""notify() core + deliveries + subscriptions + purge."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.domains.auth.models import AppUser
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.notifications.models import (
    Notification,
    NotificationDelivery,
    NotificationSubscription,
)
from app.domains.notifications.repository import NotificationsRepository
from app.domains.notifications.schemas import SubscriptionPatch
from app.domains.notifications.service import NotificationsService
from app.domains.roles.models import TenantMembership, TenantOwnership
from app.tasks.notifications import _active_tenant_owners


async def _make_tenant_and_user(db_session: AsyncSession) -> tuple:  # type: ignore[no-untyped-def]
    nick = uuid4().hex[:6]
    tenant = await FoundationService(FoundationRepository(db_session)).create_tenant(
        payload={"name": f"T-{nick}", "contact_email": f"t-{nick}@aurum.tj"}
    )
    user = AppUser(
        email=f"u-{nick}@aurum.tj",
        full_name="U",
        home_tenant_id=tenant.id,
        status="active",
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    return tenant, user


async def test_license_notifications_target_only_active_owner(
    db_session: AsyncSession,
) -> None:
    tenant, owner = await _make_tenant_and_user(db_session)
    employee = AppUser(
        email=f"employee-{uuid4().hex[:6]}@aurum.tj",
        full_name="Employee",
        home_tenant_id=tenant.id,
        status="active",
    )
    db_session.add(employee)
    await db_session.flush()

    owner_membership = TenantMembership(
        tenant_id=tenant.id,
        user_id=owner.id,
        full_name=owner.full_name,
        status="active",
    )
    employee_membership = TenantMembership(
        tenant_id=tenant.id,
        user_id=employee.id,
        full_name=employee.full_name,
        status="active",
    )
    db_session.add_all([owner_membership, employee_membership])
    await db_session.flush()
    db_session.add(
        TenantOwnership(
            tenant_id=tenant.id,
            membership_id=owner_membership.id,
        )
    )
    await db_session.flush()

    recipients = await _active_tenant_owners(db_session, tenant_id=tenant.id)

    assert [user.id for user in recipients] == [owner.id]


async def test_notify_creates_notification_default_channels(
    db_session: AsyncSession,
) -> None:
    tenant, user = await _make_tenant_and_user(db_session)
    service = NotificationsService(NotificationsRepository(db_session))

    n = await service.notify(
        tenant_id=tenant.id,
        user_id=user.id,
        event_type="invoice_due",
        title="Счёт на оплату",
        body="Срок — через 7 дней",
        severity="info",
    )
    assert n is not None
    assert n.event_type == "invoice_due"

    # Default subscription = ["in_app"] only → no deliveries queued
    deliveries = (
        (
            await db_session.execute(
                select(NotificationDelivery).where(NotificationDelivery.notification_id == n.id)
            )
        )
        .scalars()
        .all()
    )
    assert deliveries == []


async def test_subscription_with_email_queues_delivery(
    db_session: AsyncSession,
) -> None:
    tenant, user = await _make_tenant_and_user(db_session)
    service = NotificationsService(NotificationsRepository(db_session))

    await service.upsert_subscriptions(
        user_id=user.id,
        items=[
            {
                "event_type": "trial_ending",
                "channels": ["in_app", "email"],
                "is_enabled": True,
            }
        ],
    )
    n = await service.notify(
        tenant_id=tenant.id,
        user_id=user.id,
        event_type="trial_ending",
        title="Trial кончается",
        severity="warning",
    )
    assert n is not None

    deliveries = (
        (
            await db_session.execute(
                select(NotificationDelivery).where(NotificationDelivery.notification_id == n.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(deliveries) == 1
    assert deliveries[0].channel == "email"
    assert deliveries[0].recipient == user.email
    assert deliveries[0].status == "pending"


async def test_disabled_subscription_skips_notify(
    db_session: AsyncSession,
) -> None:
    tenant, user = await _make_tenant_and_user(db_session)
    service = NotificationsService(NotificationsRepository(db_session))

    await service.upsert_subscriptions(
        user_id=user.id,
        items=[
            {
                "event_type": "import_completed",
                "channels": ["in_app"],
                "is_enabled": False,
            }
        ],
    )
    result = await service.notify(
        tenant_id=tenant.id,
        user_id=user.id,
        event_type="import_completed",
        title="Импорт завершён",
        severity="info",
    )
    assert result is None

    found = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == user.id,
                    Notification.event_type == "import_completed",
                )
            )
        )
        .scalars()
        .all()
    )
    assert found == []


async def test_mandatory_security_subscription_cannot_be_disabled(
    db_session: AsyncSession,
) -> None:
    _tenant, user = await _make_tenant_and_user(db_session)
    service = NotificationsService(NotificationsRepository(db_session))

    with pytest.raises(ValidationError):
        SubscriptionPatch(
            event_type="security.new_device_login",
            channels=[],
            is_enabled=False,
        )

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                NotificationSubscription(
                    user_id=user.id,
                    event_type="security.new_device_login",
                    channels=[],
                    is_enabled=False,
                )
            )
            await db_session.flush()

    updated = await service.upsert_subscriptions(
        user_id=user.id,
        items=[
            {
                "event_type": "security.new_device_login",
                "channels": [],
                "is_enabled": False,
            }
        ],
    )
    assert updated[0].is_enabled is True
    assert updated[0].channels == ["in_app"]


async def test_attempt_delivery_marks_sent(db_session: AsyncSession) -> None:
    """Phase-1 stub: attempt_delivery flips status to 'sent' on first try."""
    tenant, user = await _make_tenant_and_user(db_session)
    service = NotificationsService(NotificationsRepository(db_session))

    await service.upsert_subscriptions(
        user_id=user.id,
        items=[{"event_type": "evt", "channels": ["in_app", "email"]}],
    )
    n = await service.notify(
        tenant_id=tenant.id,
        user_id=user.id,
        event_type="evt",
        title="t",
    )
    assert n is not None
    delivery = (
        await db_session.execute(
            select(NotificationDelivery).where(NotificationDelivery.notification_id == n.id)
        )
    ).scalar_one()
    assert delivery.status == "pending"

    updated = await service.attempt_delivery(delivery)
    assert updated.status == "sent"
    assert updated.attempts == 1
    assert updated.sent_at is not None


async def test_mark_read_and_all(db_session: AsyncSession) -> None:
    tenant, user = await _make_tenant_and_user(db_session)
    service = NotificationsService(NotificationsRepository(db_session))

    a = await service.notify(tenant_id=tenant.id, user_id=user.id, event_type="x", title="a")
    b = await service.notify(tenant_id=tenant.id, user_id=user.id, event_type="x", title="b")
    assert a is not None and b is not None

    assert await service.mark_read(notification_id=a.id, user_id=user.id) is True
    # Second call returns False — already read
    assert await service.mark_read(notification_id=a.id, user_id=user.id) is False

    n = await service.mark_all_read(user_id=user.id)
    assert n == 1  # only `b` left unread


async def test_purge_old_read(db_session: AsyncSession) -> None:
    """purge_old() drops READ notifications older than the cutoff."""
    tenant, user = await _make_tenant_and_user(db_session)
    service = NotificationsService(NotificationsRepository(db_session))

    n = await service.notify(tenant_id=tenant.id, user_id=user.id, event_type="old", title="t")
    assert n is not None
    # Mark read and back-date the read_at into the past
    await service.mark_read(notification_id=n.id, user_id=user.id)
    await db_session.execute(
        Notification.__table__.update()
        .where(Notification.id == n.id)
        .values(read_at=utc_now() - timedelta(days=60))
    )

    removed = await service.purge_old(older_than=utc_now() - timedelta(days=30))
    assert removed == 1
