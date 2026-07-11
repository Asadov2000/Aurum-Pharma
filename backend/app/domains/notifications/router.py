"""FastAPI endpoints for the notifications domain."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, current_user, get_db
from app.core.errors import NotFoundError
from app.domains.notifications.repository import NotificationsRepository
from app.domains.notifications.schemas import (
    NotificationRead,
    SubscriptionRead,
    SubscriptionsBulkPatch,
)
from app.domains.notifications.service import NotificationsService

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> NotificationsService:
    return NotificationsService(NotificationsRepository(db))


@router.get("", response_model=list[NotificationRead])
async def list_notifications(
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[NotificationsService, Depends(_service)],
    unread_only: Annotated[bool, Query()] = False,
    severity: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[NotificationRead]:
    items = await service.list_for_user(
        user_id=user.user_id,
        unread_only=unread_only,
        severity=severity,
        page=page,
        page_size=page_size,
    )
    return [NotificationRead.model_validate(i) for i in items]


@router.post("/{notification_id}/read")
async def mark_one_read(
    notification_id: UUID,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[NotificationsService, Depends(_service)],
) -> dict[str, str]:
    ok = await service.mark_read(notification_id=notification_id, user_id=user.user_id)
    if not ok:
        raise NotFoundError("Notification not found or already read")
    return {"status": "read"}


@router.post("/read-all")
async def mark_all_read(
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[NotificationsService, Depends(_service)],
) -> dict[str, int]:
    n = await service.mark_all_read(user_id=user.user_id)
    return {"marked": n}


@router.get("/subscriptions", response_model=list[SubscriptionRead])
async def list_subscriptions(
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[NotificationsService, Depends(_service)],
) -> list[SubscriptionRead]:
    subs = await service.list_subscriptions(user.user_id)
    return [SubscriptionRead.model_validate(s) for s in subs]


@router.patch("/subscriptions", response_model=list[SubscriptionRead])
async def patch_subscriptions(
    payload: SubscriptionsBulkPatch,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[NotificationsService, Depends(_service)],
) -> list[SubscriptionRead]:
    items = [s.model_dump() for s in payload.items]
    updated = await service.upsert_subscriptions(user_id=user.user_id, items=items)
    return [SubscriptionRead.model_validate(s) for s in updated]
