"""Developer-only API for protected platform access grants."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    CurrentUser,
    get_db,
    require_recent_platform_capability,
)
from app.core.errors import AuthenticationError
from app.domains.platform_access.repository import (
    PlatformAccessGrantRecord,
    PlatformAccessRepository,
)
from app.domains.platform_access.schemas import (
    PlatformAccessApproval,
    PlatformAccessGrantList,
    PlatformAccessGrantRead,
    PlatformAccessKind,
    PlatformAccessRequest,
    PlatformAccessRevocation,
    PlatformAccessStatus,
    PlatformCapabilityRead,
)
from app.domains.platform_access.service import PlatformAccessService

router = APIRouter(prefix="/api/v1/admin/platform-access", tags=["platform-access"])


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> PlatformAccessService:
    return PlatformAccessService(PlatformAccessRepository(db))


def _auth_session_id(user: CurrentUser) -> UUID:
    if user.session_id is None:
        raise AuthenticationError("Authentication session is unavailable")
    return user.session_id


def _read(record: PlatformAccessGrantRecord) -> PlatformAccessGrantRead:
    return PlatformAccessGrantRead(**record.__dict__)


@router.get("/capabilities", response_model=list[PlatformCapabilityRead])
async def list_platform_capabilities(
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.access.view")),
    ],
    service: Annotated[PlatformAccessService, Depends(_service)],
    access_kind: Annotated[PlatformAccessKind, Query()],
) -> list[PlatformCapabilityRead]:
    capabilities = await service.list_capabilities(
        actor_user_id=user.user_id,
        actor_session_id=_auth_session_id(user),
        actor_is_developer=user.is_developer,
        access_kind=access_kind.value,
    )
    return [PlatformCapabilityRead(**capability.__dict__) for capability in capabilities]


@router.get("/grants", response_model=PlatformAccessGrantList)
async def list_platform_access_grants(
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.access.view")),
    ],
    service: Annotated[PlatformAccessService, Depends(_service)],
    grant_status: Annotated[PlatformAccessStatus | None, Query(alias="status")] = None,
    user_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> PlatformAccessGrantList:
    grants = await service.list_grants(
        actor_user_id=user.user_id,
        actor_session_id=_auth_session_id(user),
        actor_is_developer=user.is_developer,
        status=grant_status.value if grant_status is not None else None,
        user_id=user_id,
        limit=limit,
    )
    return PlatformAccessGrantList(items=[_read(grant) for grant in grants])


@router.post(
    "/grants",
    response_model=PlatformAccessGrantRead,
    status_code=status.HTTP_201_CREATED,
)
async def request_platform_access(
    payload: PlatformAccessRequest,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.access.manage")),
    ],
    service: Annotated[PlatformAccessService, Depends(_service)],
) -> PlatformAccessGrantRead:
    grant = await service.request_grant(
        actor_user_id=user.user_id,
        actor_session_id=_auth_session_id(user),
        actor_is_developer=user.is_developer,
        user_id=payload.user_id,
        access_kind=payload.access_kind.value,
        reason_code=payload.reason_code.value,
        reason=payload.reason,
        capabilities=tuple(capability.value for capability in payload.capabilities),
    )
    return _read(grant)


@router.post(
    "/grants/{grant_id}/approve",
    response_model=PlatformAccessGrantRead,
)
async def approve_platform_access(
    grant_id: UUID,
    payload: PlatformAccessApproval,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.access.manage")),
    ],
    service: Annotated[PlatformAccessService, Depends(_service)],
) -> PlatformAccessGrantRead:
    grant = await service.approve_grant(
        actor_user_id=user.user_id,
        actor_session_id=_auth_session_id(user),
        actor_is_developer=user.is_developer,
        grant_id=grant_id,
        version=payload.version,
        reason_code=payload.reason_code.value,
        reason=payload.reason,
    )
    return _read(grant)


@router.post(
    "/grants/{grant_id}/revoke",
    response_model=PlatformAccessGrantRead,
)
async def revoke_platform_access(
    grant_id: UUID,
    payload: PlatformAccessRevocation,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.access.manage")),
    ],
    service: Annotated[PlatformAccessService, Depends(_service)],
) -> PlatformAccessGrantRead:
    grant = await service.revoke_grant(
        actor_user_id=user.user_id,
        actor_session_id=_auth_session_id(user),
        actor_is_developer=user.is_developer,
        grant_id=grant_id,
        version=payload.version,
        reason_code=payload.reason_code.value,
        reason=payload.reason,
    )
    return _read(grant)
