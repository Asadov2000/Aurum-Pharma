"""Business logic for the notifications domain.

`notify(...)` is the single entry-point that other domains call:

    await NotificationsService(repo).notify(
        tenant_id=..., user_id=..., event_type="invoice_overdue",
        title="Invoice overdue", body="...", severity="warning",
    )

It writes the in-app notification, looks up the user's subscription
for this event_type, and for every non-`in_app` channel listed there
queues a `notification_delivery(status='pending')`. The actual
sending happens in the Celery worker (`process_pending_deliveries`).

`channels` defaults to `["in_app"]` if the user has no subscription
on record — that matches the spec ("on by default, opt-out to
silence"). Setting `is_enabled=False` short-circuits everything.

Cross-domain callers will use a lazy import:

    from app.domains.notifications.service import notify_helper
    await notify_helper(session, tenant_id=..., ...)

so the load DAG stays acyclic.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.domains.auth.models import AppUser
from app.domains.notifications.models import (
    Notification,
    NotificationDelivery,
    NotificationSubscription,
)
from app.domains.notifications.repository import NotificationsRepository

logger = structlog.get_logger("notifications.service")

DEFAULT_RETENTION = timedelta(days=30)
MAX_DELIVERY_ATTEMPTS = 3


class NotificationsService:
    def __init__(self, repo: NotificationsRepository) -> None:
        self.repo = repo

    # =========================================================================
    # Inbox reads
    # =========================================================================

    async def list_for_user(
        self,
        *,
        user_id: UUID,
        unread_only: bool = False,
        severity: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> list[Notification]:
        return await self.repo.list_for_user(
            user_id=user_id,
            unread_only=unread_only,
            severity=severity,
            page=page,
            page_size=page_size,
        )

    async def mark_read(self, *, notification_id: UUID, user_id: UUID) -> bool:
        rows = await self.repo.mark_read(
            notification_id=notification_id,
            user_id=user_id,
            when=utc_now(),
        )
        return rows > 0

    async def mark_all_read(self, *, user_id: UUID) -> int:
        return await self.repo.mark_all_read(user_id=user_id, when=utc_now())

    # =========================================================================
    # Subscriptions
    # =========================================================================

    async def list_subscriptions(self, user_id: UUID) -> list[NotificationSubscription]:
        return await self.repo.list_subscriptions(user_id)

    async def upsert_subscriptions(
        self,
        *,
        user_id: UUID,
        items: list[dict[str, Any]],
    ) -> list[NotificationSubscription]:
        out: list[NotificationSubscription] = []
        for item in items:
            sub = await self.repo.upsert_subscription(
                user_id=user_id,
                event_type=item["event_type"],
                channels=item.get("channels", ["in_app"]),
                is_enabled=item.get("is_enabled", True),
            )
            out.append(sub)
        return out

    # =========================================================================
    # Notify — the only write API the rest of the app should call
    # =========================================================================

    async def notify(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        event_type: str,
        title: str,
        body: str | None = None,
        data: dict[str, Any] | None = None,
        severity: str = "info",
    ) -> Notification | None:
        """Returns the created Notification, or None if the user opted
        out of this event_type (is_enabled=False)."""
        sub = await self.repo.get_subscription(user_id=user_id, event_type=event_type)
        if sub is not None and not sub.is_enabled:
            logger.info(
                "notify_skipped_opt_out",
                user_id=str(user_id),
                event_type=event_type,
            )
            return None
        channels: list[str] = list(sub.channels) if sub is not None else ["in_app"]

        notification = await self.repo.create_notification(
            tenant_id=tenant_id,
            user_id=user_id,
            event_type=event_type,
            title=title,
            body=body,
            data=data,
            severity=severity,
        )

        # Out-of-band channels — queue deliveries. Pulling the recipient from
        # AppUser here so the worker doesn't need to re-resolve it.
        user = await self.repo.session.get(AppUser, user_id)
        for channel in channels:
            if channel == "in_app":
                continue
            recipient = self._recipient_for(channel, user)
            if recipient is None:
                logger.info(
                    "notify_no_recipient",
                    channel=channel,
                    user_id=str(user_id),
                )
                continue
            await self.repo.insert_delivery(
                notification_id=notification.id,
                channel=channel,
                recipient=recipient,
            )

        logger.info(
            "notify_created",
            notification_id=str(notification.id),
            event_type=event_type,
            severity=severity,
            channels=channels,
        )
        return notification

    @staticmethod
    def _recipient_for(channel: str, user: AppUser | None) -> str | None:
        if user is None:
            return None
        if channel == "email":
            return user.email
        # telegram / sms recipients live in user_profile fields that exist
        # only in phase 2. Until then we cannot dispatch them.
        return None

    # =========================================================================
    # Worker-side: try to send a pending delivery
    # =========================================================================

    async def attempt_delivery(self, delivery: NotificationDelivery) -> NotificationDelivery:
        """Phase-1 stub: logs the payload, marks the row as sent.
        Phase-2 implementation will plug in SMTP / Telegram bot API and
        keep the retry loop (already encoded by attempts < MAX_ATTEMPTS).
        """
        now = utc_now()
        attempts = delivery.attempts + 1
        try:
            # No real network call yet — phase 1 is log-only.
            logger.info(
                "delivery_attempt",
                delivery_id=str(delivery.id),
                channel=delivery.channel,
                attempt=attempts,
            )
            return await self.repo.update_delivery(
                delivery,
                status="sent",
                attempts=attempts,
                sent_at=now,
                error_message=None,
            )
        except Exception as exc:
            new_status = "failed" if attempts >= MAX_DELIVERY_ATTEMPTS else "pending"
            return await self.repo.update_delivery(
                delivery,
                status=new_status,
                attempts=attempts,
                error_message=str(exc),
            )

    async def purge_old(self, *, older_than: datetime | None = None) -> int:
        cutoff = older_than or (utc_now() - DEFAULT_RETENTION)
        return await self.repo.delete_old_read(older_than=cutoff)


# -----------------------------------------------------------------------------
# Lazy helper for cross-domain notifies — kept here so callers stay one-liners.
# -----------------------------------------------------------------------------


async def notify_helper(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    event_type: str,
    title: str,
    body: str | None = None,
    data: dict[str, Any] | None = None,
    severity: str = "info",
) -> Notification | None:
    service = NotificationsService(NotificationsRepository(session))
    return await service.notify(
        tenant_id=tenant_id,
        user_id=user_id,
        event_type=event_type,
        title=title,
        body=body,
        data=data,
        severity=severity,
    )
