"""Platform team invitation and activation endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import (
    CurrentUser,
    get_auth_db,
    get_db,
    require_platform_capability,
    require_recent_platform_capability,
)
from app.core.errors import AuthenticationError
from app.domains.platform_accounts.repository import PlatformAccountsRepository
from app.domains.platform_accounts.schemas import (
    PlatformStaffAccountList,
    PlatformStaffAccountRead,
    PlatformStaffActivationRead,
    PlatformStaffActivationRequest,
    PlatformStaffInvitationCreate,
    PlatformStaffInvitationRead,
    PlatformStaffStatus,
)
from app.domains.platform_accounts.service import PlatformAccountsService

admin_router = APIRouter(prefix="/api/v1/admin/platform-accounts", tags=["platform-accounts"])
activation_router = APIRouter(prefix="/api/v1/auth/platform-activation", tags=["auth"])


def _auth_session_id(user: CurrentUser) -> UUID:
    if user.session_id is None:
        raise AuthenticationError("Authentication session is unavailable")
    return user.session_id


async def _admin_service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> PlatformAccountsService:
    return PlatformAccountsService(
        PlatformAccountsRepository(db),
        expose_activation_token=get_settings().AUTH_LOCAL_TESTING_MODE,
    )


async def _activation_service(
    db: Annotated[AsyncSession, Depends(get_auth_db, scope="function")],
) -> PlatformAccountsService:
    return PlatformAccountsService(PlatformAccountsRepository(db))


def _read(record: object) -> PlatformStaffAccountRead:
    return PlatformStaffAccountRead.model_validate(record, from_attributes=True)


@admin_router.get("", response_model=PlatformStaffAccountList)
async def list_platform_accounts(
    user: Annotated[
        CurrentUser,
        Depends(require_platform_capability("platform.accounts.view")),
    ],
    service: Annotated[PlatformAccountsService, Depends(_admin_service)],
    query: Annotated[str | None, Query(alias="q", min_length=2, max_length=100)] = None,
    account_status: Annotated[PlatformStaffStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PlatformStaffAccountList:
    accounts, total = await service.list_accounts(
        actor_user_id=user.user_id,
        actor_session_id=_auth_session_id(user),
        query=query,
        status=account_status.value if account_status is not None else None,
        limit=limit,
        offset=offset,
    )
    return PlatformStaffAccountList(items=[_read(account) for account in accounts], total=total)


@admin_router.post(
    "/invitations",
    response_model=PlatformStaffInvitationRead,
    status_code=status.HTTP_201_CREATED,
)
async def invite_platform_account(
    payload: PlatformStaffInvitationCreate,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.accounts.manage")),
    ],
    service: Annotated[PlatformAccountsService, Depends(_admin_service)],
) -> PlatformStaffInvitationRead:
    invitation = await service.invite(
        actor_user_id=user.user_id,
        actor_session_id=_auth_session_id(user),
        email=str(payload.email),
        full_name=payload.full_name,
    )
    return PlatformStaffInvitationRead(
        **_read(invitation.account).model_dump(),
        activation_token=invitation.activation_token,
    )


@activation_router.post("", response_model=PlatformStaffActivationRead)
async def activate_platform_account(
    payload: PlatformStaffActivationRequest,
    service: Annotated[PlatformAccountsService, Depends(_activation_service)],
) -> PlatformStaffActivationRead:
    await service.activate(token=payload.token, password=payload.password)
    return PlatformStaffActivationRead()
