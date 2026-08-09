"""Platform API for starting and ending scoped tenant support access."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    CurrentUser,
    get_db,
    require_platform_capability,
    require_recent_platform_capability,
)
from app.core.errors import AuthenticationError
from app.domains.support_access.repository import (
    SupportAccessRepository,
    SupportAccessSessionRecord,
)
from app.domains.support_access.schemas import (
    SupportAccessSessionCreate,
    SupportAccessSessionList,
    SupportAccessSessionRead,
    SupportAccessSessionRevoke,
    SupportCapabilityRead,
)
from app.domains.support_access.service import SupportAccessService

router = APIRouter(prefix="/api/v1/admin/support-access", tags=["support-access"])


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> SupportAccessService:
    return SupportAccessService(SupportAccessRepository(db))


def _read(record: SupportAccessSessionRecord) -> SupportAccessSessionRead:
    return SupportAccessSessionRead(
        id=record.id,
        tenant_id=record.tenant_id,
        tenant_name=record.tenant_name,
        actor_user_id=record.actor_user_id,
        reason=record.reason,
        capabilities=list(record.capabilities),
        is_read_only=record.is_read_only,
        started_at=record.started_at,
        expires_at=record.expires_at,
        revoked_at=record.revoked_at,
    )


def _auth_session_id(user: CurrentUser) -> UUID:
    if user.session_id is None:
        raise AuthenticationError("Authentication session is unavailable")
    return user.session_id


@router.get("/capabilities", response_model=list[SupportCapabilityRead])
async def list_support_capabilities(
    user: Annotated[
        CurrentUser,
        Depends(require_platform_capability("platform.support.use")),
    ],
    service: Annotated[SupportAccessService, Depends(_service)],
) -> list[SupportCapabilityRead]:
    capabilities = await service.list_capabilities(
        actor_is_developer=user.is_developer,
        actor_is_administrator=user.is_administrator,
    )
    return [SupportCapabilityRead(**capability.__dict__) for capability in capabilities]


@router.get("/sessions", response_model=SupportAccessSessionList)
async def list_support_sessions(
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.support.use")),
    ],
    service: Annotated[SupportAccessService, Depends(_service)],
) -> SupportAccessSessionList:
    sessions = await service.list_active_sessions(
        actor_user_id=user.user_id,
        actor_session_id=_auth_session_id(user),
    )
    return SupportAccessSessionList(items=[_read(session) for session in sessions])


@router.post(
    "/sessions",
    response_model=SupportAccessSessionRead,
    status_code=status.HTTP_201_CREATED,
)
async def start_support_session(
    payload: SupportAccessSessionCreate,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.support.use")),
    ],
    service: Annotated[SupportAccessService, Depends(_service)],
) -> SupportAccessSessionRead:
    session = await service.start_session(
        actor_user_id=user.user_id,
        actor_session_id=_auth_session_id(user),
        actor_is_developer=user.is_developer,
        actor_is_administrator=user.is_administrator,
        tenant_id=payload.tenant_id,
        reason=payload.reason,
        duration_minutes=payload.duration_minutes,
        requested_capabilities=payload.capabilities,
    )
    return _read(session)


@router.delete(
    "/sessions/{session_id}",
    response_model=SupportAccessSessionRevoke,
)
async def revoke_support_session(
    session_id: UUID,
    user: Annotated[
        CurrentUser,
        Depends(require_platform_capability("platform.support.use")),
    ],
    service: Annotated[SupportAccessService, Depends(_service)],
) -> SupportAccessSessionRevoke:
    await service.revoke_session(
        actor_user_id=user.user_id,
        actor_session_id=_auth_session_id(user),
        session_id=session_id,
    )
    return SupportAccessSessionRevoke()
