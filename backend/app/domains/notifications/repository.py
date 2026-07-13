"""DB access for the notifications domain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.notifications.models import (
    Notification,
    NotificationDelivery,
    NotificationSubscription,
)


@dataclass(frozen=True)
class ResolvedSubscription:
    channels: list[str]
    is_enabled: bool


class NotificationsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- notifications ----

    async def create_notification(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        event_type: str,
        title: str,
        body: str | None,
        data: dict[str, Any] | None,
        severity: str,
    ) -> Notification:
        stmt = select(Notification).from_statement(
            text(
                "SELECT * FROM public.create_scoped_notification("
                ":tenant_id, :user_id, :event_type, :title, :body, :data, :severity)"
            )
        )
        result = await self.session.execute(
            stmt,
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "event_type": event_type,
                "title": title,
                "body": body,
                "data": data,
                "severity": severity,
            },
        )
        return cast(Notification, result.scalar_one())

    async def get_notification(self, notification_id: UUID) -> Notification | None:
        return await self.session.get(
            Notification,
            notification_id,
            populate_existing=True,
        )

    async def list_for_user(
        self,
        *,
        user_id: UUID,
        unread_only: bool = False,
        severity: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> list[Notification]:
        stmt = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        if severity:
            stmt = stmt.where(Notification.severity == severity)
        stmt = (
            stmt.order_by(Notification.created_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_read(self, *, notification_id: UUID, user_id: UUID, when: datetime) -> int:
        result = await self.session.execute(
            text(
                "SELECT public.mark_scoped_notification_read(" ":notification_id, :user_id, :when)"
            ),
            {"notification_id": notification_id, "user_id": user_id, "when": when},
        )
        return int(result.scalar_one())

    async def mark_all_read(self, *, user_id: UUID, when: datetime) -> int:
        result = await self.session.execute(
            text("SELECT public.mark_all_scoped_notifications_read(" ":user_id, :when)"),
            {"user_id": user_id, "when": when},
        )
        return int(result.scalar_one())

    async def delete_old_read(self, *, older_than: datetime) -> int:
        from sqlalchemy import delete

        result = await self.session.execute(
            delete(Notification).where(
                and_(
                    Notification.read_at.is_not(None),
                    Notification.read_at < older_than,
                )
            )
        )
        return result.rowcount or 0  # type: ignore[attr-defined]

    # ---- subscriptions ----

    async def list_subscriptions(self, user_id: UUID) -> list[NotificationSubscription]:
        stmt = (
            select(NotificationSubscription)
            .where(NotificationSubscription.user_id == user_id)
            .order_by(NotificationSubscription.event_type.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_subscription(
        self, *, user_id: UUID, event_type: str
    ) -> NotificationSubscription | None:
        return await self.session.get(
            NotificationSubscription, {"user_id": user_id, "event_type": event_type}
        )

    async def resolve_subscription(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        event_type: str,
    ) -> ResolvedSubscription | None:
        result = await self.session.execute(
            text(
                "SELECT channels, is_enabled "
                "FROM public.resolve_notification_subscription("
                ":tenant_id, :user_id, :event_type)"
            ),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "event_type": event_type,
            },
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return ResolvedSubscription(
            channels=cast(list[str], row["channels"]),
            is_enabled=bool(row["is_enabled"]),
        )

    async def upsert_subscription(
        self,
        *,
        user_id: UUID,
        event_type: str,
        channels: list[str],
        is_enabled: bool,
    ) -> NotificationSubscription:
        sub = await self.get_subscription(user_id=user_id, event_type=event_type)
        if sub is None:
            sub = NotificationSubscription(
                user_id=user_id,
                event_type=event_type,
                channels=channels,
                is_enabled=is_enabled,
            )
            self.session.add(sub)
        else:
            sub.channels = channels
            sub.is_enabled = is_enabled
        await self.session.flush()
        await self.session.refresh(sub)
        return sub

    # ---- deliveries ----

    async def insert_delivery(self, *, notification_id: UUID, channel: str) -> UUID:
        result = await self.session.execute(
            text("SELECT public.enqueue_notification_delivery(" ":notification_id, :channel)"),
            {"notification_id": notification_id, "channel": channel},
        )
        return cast(UUID, result.scalar_one())

    async def list_pending_deliveries(self, *, limit: int = 50) -> list[NotificationDelivery]:
        stmt = (
            select(NotificationDelivery)
            .where(NotificationDelivery.status == "pending")
            .order_by(NotificationDelivery.created_at.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_delivery(
        self, delivery: NotificationDelivery, **fields: Any
    ) -> NotificationDelivery:
        for k, v in fields.items():
            setattr(delivery, k, v)
        await self.session.flush()
        await self.session.refresh(delivery)
        return delivery
