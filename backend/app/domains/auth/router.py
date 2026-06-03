"""FastAPI endpoints for the auth domain."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import CurrentUser, current_user, get_db, get_redis
from app.domains.auth.repository import AuthRepository
from app.domains.auth.schemas import (
    LoginCodeRequest,
    LoginCodeResponse,
    LoginCodeVerify,
    LogoutRequest,
    LogoutResponse,
    MeResponse,
    RefreshRequest,
    TokenResponse,
)
from app.domains.auth.service import AuthService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    # Trust the closest source first. When the API sits behind a reverse proxy
    # the proxy must populate X-Forwarded-For — only then do we honour it.
    if request.client and request.client.host:
        return request.client.host
    return "0.0.0.0"


async def _service(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> AuthService:
    return AuthService(AuthRepository(db), redis=redis)


@router.post(
    "/login/code",
    response_model=LoginCodeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_login_code(
    payload: LoginCodeRequest,
    request: Request,
    service: Annotated[AuthService, Depends(_service)],
) -> LoginCodeResponse:
    code = await service.request_login_code(
        email=str(payload.email),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    dev_code = code if get_settings().ENVIRONMENT == "development" else None
    return LoginCodeResponse(dev_code=dev_code)


@router.post("/login/verify", response_model=TokenResponse)
async def verify_login_code(
    payload: LoginCodeVerify,
    request: Request,
    service: Annotated[AuthService, Depends(_service)],
) -> TokenResponse:
    access, refresh, expires = await service.verify_login_code(
        email=str(payload.email),
        code=payload.code,
        password=payload.password,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return TokenResponse(access_token=access, refresh_token=refresh, expires_in=expires)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    payload: RefreshRequest,
    request: Request,
    service: Annotated[AuthService, Depends(_service)],
) -> TokenResponse:
    access, refresh, expires = await service.refresh(
        refresh_token=payload.refresh_token,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return TokenResponse(access_token=access, refresh_token=refresh, expires_in=expires)


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    payload: LogoutRequest,
    service: Annotated[AuthService, Depends(_service)],
) -> LogoutResponse:
    await service.logout(payload.refresh_token)
    return LogoutResponse()


@router.get("/me", response_model=MeResponse)
async def me(
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[AuthService, Depends(_service)],
) -> MeResponse:
    info = await service.get_user_info(user.user_id)
    me_data = MeResponse.model_validate(info)
    me_data.branch_assignments = user.branch_assignments
    me_data.permissions = sorted(user.permissions)
    return me_data
