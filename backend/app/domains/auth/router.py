"""FastAPI endpoints for the auth domain."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import CurrentUser, current_user, get_db, get_redis
from app.core.errors import AuthenticationError
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

_REFRESH_COOKIE_PATH = "/api/v1/auth"


def _client_ip(request: Request) -> str:
    # Trust the closest source first. When the API sits behind a reverse proxy
    # the proxy must populate X-Forwarded-For — only then do we honour it.
    if request.client and request.client.host:
        return request.client.host
    return "0.0.0.0"


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=settings.REFRESH_TOKEN_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
        path=_REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
        path=_REFRESH_COOKIE_PATH,
    )


def _refresh_token_from_request(request: Request, payload_token: str | None = None) -> str:
    if payload_token:
        return payload_token
    token = request.cookies.get(get_settings().REFRESH_COOKIE_NAME)
    if not token:
        raise AuthenticationError("Refresh session is missing")
    return token


def _assert_cookie_refresh_origin(request: Request) -> None:
    """Defense-in-depth for cookie-auth refresh/logout endpoints.

    SameSite cookies block common cross-site cases, but an explicit Origin check
    makes the boundary visible in app code and catches permissive edge mistakes.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return
    allowed = set(get_settings().CORS_ORIGINS)
    if origin not in allowed:
        raise AuthenticationError("Refresh session origin is not allowed")


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
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
    response: Response,
    service: Annotated[AuthService, Depends(_service)],
) -> TokenResponse:
    access, refresh, expires = await service.verify_login_code(
        email=str(payload.email),
        code=payload.code,
        password=payload.password,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _set_refresh_cookie(response, refresh)
    return TokenResponse(access_token=access, expires_in=expires)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(_service)],
    payload: Annotated[RefreshRequest | None, Body()] = None,
) -> TokenResponse:
    using_cookie = payload is None or not payload.refresh_token
    if using_cookie:
        _assert_cookie_refresh_origin(request)
    refresh_token = _refresh_token_from_request(
        request,
        payload.refresh_token if payload is not None else None,
    )
    access, refresh, expires = await service.refresh(
        refresh_token=refresh_token,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _set_refresh_cookie(response, refresh)
    return TokenResponse(access_token=access, expires_in=expires)


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(_service)],
    payload: Annotated[LogoutRequest | None, Body()] = None,
) -> LogoutResponse:
    try:
        refresh_token = _refresh_token_from_request(
            request,
            payload.refresh_token if payload is not None else None,
        )
    except AuthenticationError:
        refresh_token = None
    if refresh_token:
        using_cookie = payload is None or not payload.refresh_token
        if using_cookie:
            _assert_cookie_refresh_origin(request)
        await service.logout(refresh_token)
    _clear_refresh_cookie(response)
    return LogoutResponse()


@router.get("/me", response_model=MeResponse)
async def me(
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[AuthService, Depends(_service)],
) -> MeResponse:
    info = await service.get_user_info(user.user_id)
    me_data = MeResponse.model_validate(info)
    me_data.level = user.level
    me_data.branch_assignments = user.branch_assignments
    me_data.permissions = sorted(user.permissions)
    return me_data
