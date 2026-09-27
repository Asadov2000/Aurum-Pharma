"""FastAPI endpoints for the auth domain."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import (
    CurrentUser,
    current_user,
    get_auth_db,
    get_db,
    get_redis,
    get_support_auth_db,
)
from app.core.errors import AuthenticationError, ConflictError
from app.core.security import generate_device_id
from app.domains.auth.identity_repository import IdentityRepository
from app.domains.auth.identity_schemas import (
    ContactCodeRequest,
    IdentityResponse,
    LoginMethodsResponse,
    PasswordLoginRequest,
    PasswordResetRequest,
    PhoneBindingRequest,
    PhoneRequest,
    PhoneVerifyRequest,
    SetupChannelRequest,
)
from app.domains.auth.identity_service import IdentityService
from app.domains.auth.models import UserPreferences
from app.domains.auth.profile_repository import ProfileRepository
from app.domains.auth.profile_schemas import ProfileResponse
from app.domains.auth.profile_service import ProfileService
from app.domains.auth.repository import AuthRepository
from app.domains.auth.schemas import (
    AccountMfaDisableRequest,
    AccountMfaEnrollmentResponse,
    AccountMfaSettingsResponse,
    AccountSecurityActionResponse,
    ActiveSessionResponse,
    LoginCodeRequest,
    LoginCodeResponse,
    LoginCodeVerify,
    LogoutRequest,
    LogoutResponse,
    MeResponse,
    MfaChallengeRequest,
    MfaChallengeResponse,
    MfaCodeRequest,
    MfaEnrollmentStartResponse,
    MfaRecoveryRequest,
    MfaStepUpRequest,
    PasswordConfirmationRequest,
    PasswordSetupRequest,
    RefreshRequest,
    SessionListResponse,
    SessionRevokeResponse,
    SupportAccessContextResponse,
    TokenResponse,
    UserPreferencesRead,
    UserPreferencesUpdate,
    WorkspacePreferences,
)
from app.domains.auth.service import AuthService, AuthTokens, MfaLoginChallenge

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_REFRESH_COOKIE_PATH = "/api/v1/auth"
_DEVICE_COOKIE_NAME = "aurum_device_id"
_DEVICE_COOKIE_MAX_AGE = 365 * 24 * 60 * 60


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


def _device_id_from_request(request: Request) -> str:
    value = request.cookies.get(_DEVICE_COOKIE_NAME, "")
    if len(value) == 64 and all(char in "0123456789abcdef" for char in value):
        return value
    return generate_device_id()


def _set_device_cookie(response: Response, device_id: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=_DEVICE_COOKIE_NAME,
        value=device_id,
        max_age=_DEVICE_COOKIE_MAX_AGE,
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


def _set_auth_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _workspace_scope(user: CurrentUser) -> str:
    return str(user.tenant_id) if user.tenant_id is not None else "global"


def _preferences_response(
    preferences: UserPreferences,
    user: CurrentUser,
) -> UserPreferencesRead:
    raw_workspace = preferences.workspace_preferences.get(_workspace_scope(user), {})
    workspace = WorkspacePreferences.model_validate(raw_workspace)
    return UserPreferencesRead.model_validate(
        {
            "theme": preferences.theme,
            "density": preferences.density,
            "contrast": preferences.contrast,
            "reduce_motion": preferences.reduce_motion,
            "accent": preferences.accent,
            "workspace": workspace,
            "version": preferences.version,
            "updated_at": preferences.updated_at,
        }
    )


def _refresh_token_from_request(request: Request) -> str:
    token = request.cookies.get(get_settings().REFRESH_COOKIE_NAME)
    if not token:
        raise AuthenticationError("Refresh session is missing")
    return token


def _assert_cookie_refresh_origin(request: Request) -> None:
    """Defense-in-depth for cookie-auth refresh/logout endpoints.

    SameSite cookies block common cross-site cases, but an explicit Origin check
    makes the boundary visible in app code and catches permissive edge mistakes.
    """
    settings = get_settings()
    origin = request.headers.get("origin")
    if origin is None:
        if settings.ENVIRONMENT != "development":
            raise AuthenticationError("Refresh session origin is required")
        return
    allowed = set(settings.CORS_ORIGINS)
    if origin not in allowed:
        raise AuthenticationError("Refresh session origin is not allowed")


async def _auth_state_service(
    db: Annotated[AsyncSession, Depends(get_auth_db, scope="function")],
    redis: Annotated[Redis, Depends(get_redis)],
) -> AuthService:
    return AuthService(AuthRepository(db), redis=redis)


async def _support_auth_state_service(
    db: Annotated[AsyncSession, Depends(get_support_auth_db, scope="function")],
    redis: Annotated[Redis, Depends(get_redis)],
) -> AuthService:
    return AuthService(AuthRepository(db), redis=redis)


async def _identity_service(
    db: Annotated[AsyncSession, Depends(get_support_auth_db, scope="function")],
    redis: Annotated[Redis, Depends(get_redis)],
) -> IdentityService:
    # Credential verification uses the same restricted auth boundary as MFA.
    # This service never accepts SQL or executes tenant business operations.
    return IdentityService(IdentityRepository(db), redis=redis)


def _identity_login_response(
    result: AuthTokens | MfaLoginChallenge, response: Response, device_id: str
) -> TokenResponse | MfaChallengeResponse:
    _set_device_cookie(response, device_id)
    if isinstance(result, MfaLoginChallenge):
        return MfaChallengeResponse(
            status=result.status,
            challenge_token=result.challenge_token,
            expires_in=result.expires_in,
        )
    _set_refresh_cookie(response, result.refresh_token)
    return TokenResponse(access_token=result.access_token, expires_in=result.expires_in)


@router.get("/methods", response_model=LoginMethodsResponse)
async def login_methods(response: Response) -> LoginMethodsResponse:
    _set_auth_no_store(response)
    return LoginMethodsResponse(phone_login_enabled=get_settings().SMS_PROVIDER != "disabled")


@router.post("/login/password", response_model=TokenResponse | MfaChallengeResponse)
async def login_password(
    payload: PasswordLoginRequest,
    request: Request,
    response: Response,
    service: Annotated[IdentityService, Depends(_identity_service)],
) -> TokenResponse | MfaChallengeResponse:
    _set_auth_no_store(response)
    device_id = _device_id_from_request(request)
    result = await service.login_password(
        email=str(payload.email),
        password=payload.password,
        ip=_client_ip(request),
        agent=request.headers.get("user-agent"),
        device_id=device_id,
    )
    return _identity_login_response(result, response, device_id)


@router.post("/login/phone/code", response_model=LoginCodeResponse, status_code=202)
async def request_phone_login(
    payload: PhoneRequest,
    request: Request,
    response: Response,
    service: Annotated[IdentityService, Depends(_identity_service)],
) -> LoginCodeResponse:
    _set_auth_no_store(response)
    code = await service.request_code(
        channel="phone",
        contact=payload.phone,
        purpose="login",
        ip=_client_ip(request),
        agent=request.headers.get("user-agent"),
    )
    return LoginCodeResponse(dev_code=code)


@router.post("/login/phone/verify", response_model=TokenResponse | MfaChallengeResponse)
async def verify_phone_login(
    payload: PhoneVerifyRequest,
    request: Request,
    response: Response,
    service: Annotated[IdentityService, Depends(_identity_service)],
) -> TokenResponse | MfaChallengeResponse:
    _set_auth_no_store(response)
    device_id = _device_id_from_request(request)
    result = await service.login_phone(
        phone=payload.phone,
        code=payload.code,
        ip=_client_ip(request),
        agent=request.headers.get("user-agent"),
        device_id=device_id,
    )
    return _identity_login_response(result, response, device_id)


@router.post("/password/reset/code", response_model=LoginCodeResponse, status_code=202)
async def request_password_reset(
    payload: ContactCodeRequest,
    request: Request,
    response: Response,
    service: Annotated[IdentityService, Depends(_identity_service)],
) -> LoginCodeResponse:
    _set_auth_no_store(response)
    code = await service.request_code(
        channel=payload.channel,
        contact=payload.contact,
        purpose="reset",
        ip=_client_ip(request),
        agent=request.headers.get("user-agent"),
    )
    return LoginCodeResponse(dev_code=code)


@router.post("/password/reset", response_model=AccountSecurityActionResponse)
async def reset_password(
    payload: PasswordResetRequest,
    request: Request,
    response: Response,
    service: Annotated[IdentityService, Depends(_identity_service)],
) -> AccountSecurityActionResponse:
    _set_auth_no_store(response)
    await service.change_password(
        channel=payload.channel,
        contact=payload.contact,
        code=payload.code,
        new_password=payload.new_password,
        mfa_code=payload.mfa_code,
        recovery_code=payload.recovery_code,
        ip=_client_ip(request),
    )
    _clear_refresh_cookie(response)
    return AccountSecurityActionResponse()


@router.get("/identity", response_model=IdentityResponse)
async def account_identity(
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[IdentityService, Depends(_identity_service)],
) -> IdentityResponse:
    _set_auth_no_store(response)
    return IdentityResponse.model_validate(
        await service.identity_settings(user.user_id, _account_session_id(user))
    )


@router.post("/password/prompt/dismiss", response_model=AccountSecurityActionResponse)
async def dismiss_password_prompt(
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[IdentityService, Depends(_identity_service)],
) -> AccountSecurityActionResponse:
    _set_auth_no_store(response)
    if not await service.repo.dismiss_password_prompt(user.user_id, _account_session_id(user)):
        raise AuthenticationError("Authenticated session is inactive")
    return AccountSecurityActionResponse()


@router.post("/phone/reauth/code", response_model=LoginCodeResponse, status_code=202)
async def request_phone_reauth(
    request: Request,
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[IdentityService, Depends(_identity_service)],
) -> LoginCodeResponse:
    _set_auth_no_store(response)
    identity = await service.identity_settings(user.user_id, _account_session_id(user))
    if identity["has_password"]:
        raise AuthenticationError("Confirm your password")
    code = await service.request_code(
        channel="email",
        contact=str(identity["email"]),
        purpose="reauth",
        ip=_client_ip(request),
        agent=request.headers.get("user-agent"),
        user_id=user.user_id,
        session_id=_account_session_id(user),
    )
    return LoginCodeResponse(dev_code=code)


@router.post("/phone/code", response_model=LoginCodeResponse, status_code=202)
async def request_phone_binding(
    payload: PhoneBindingRequest,
    request: Request,
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[IdentityService, Depends(_identity_service)],
) -> LoginCodeResponse:
    _set_auth_no_store(response)
    code = await service.request_phone_binding(
        user_id=user.user_id,
        session_id=_account_session_id(user),
        phone=payload.phone,
        password=payload.password,
        reauth_code=payload.reauth_code,
        mfa_code=payload.mfa_code,
        ip=_client_ip(request),
        agent=request.headers.get("user-agent"),
    )
    return LoginCodeResponse(dev_code=code)


@router.post("/phone/verify", response_model=AccountSecurityActionResponse)
async def verify_phone_binding(
    payload: PhoneVerifyRequest,
    request: Request,
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[IdentityService, Depends(_identity_service)],
) -> AccountSecurityActionResponse:
    _set_auth_no_store(response)
    await service.bind_phone(
        user_id=user.user_id,
        session_id=_account_session_id(user),
        phone=payload.phone,
        code=payload.code,
        ip=_client_ip(request),
    )
    return AccountSecurityActionResponse()


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
    response: Response,
    service: Annotated[AuthService, Depends(_auth_state_service)],
) -> LoginCodeResponse:
    _set_auth_no_store(response)
    code = await service.request_login_code(
        email=str(payload.email),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    dev_code = code if get_settings().ENVIRONMENT == "development" else None
    return LoginCodeResponse(dev_code=dev_code)


@router.post(
    "/login/verify",
    response_model=TokenResponse | MfaChallengeResponse,
)
async def verify_login_code(
    payload: LoginCodeVerify,
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(_auth_state_service)],
) -> TokenResponse | MfaChallengeResponse:
    _set_auth_no_store(response)
    device_id = _device_id_from_request(request)
    result = await service.verify_login_code(
        email=str(payload.email),
        code=payload.code,
        password=payload.password,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        device_id=device_id,
    )
    _set_device_cookie(response, device_id)
    if isinstance(result, MfaLoginChallenge):
        return MfaChallengeResponse(
            status=result.status,
            challenge_token=result.challenge_token,
            expires_in=result.expires_in,
        )
    _set_refresh_cookie(response, result.refresh_token)
    return TokenResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
    )


def _account_session_id(user: CurrentUser) -> UUID:
    if user.session_id is None:
        raise AuthenticationError("Authenticated session is missing")
    return user.session_id


@router.get("/mfa/settings", response_model=AccountMfaSettingsResponse)
async def get_account_mfa_settings(
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[AuthService, Depends(_support_auth_state_service)],
) -> AccountMfaSettingsResponse:
    _set_auth_no_store(response)
    record = await service.account_mfa_settings(
        user_id=user.user_id, session_id=_account_session_id(user)
    )
    enabled = record.status in {"active", "recovery_pending"}
    return AccountMfaSettingsResponse(
        enabled=enabled,
        prompt_pending=not enabled and record.prompt_dismissed_at is None,
        has_password=record.password_configured,
    )


@router.post("/mfa/settings/dismiss", response_model=AccountSecurityActionResponse)
async def dismiss_account_mfa_prompt(
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[AuthService, Depends(_support_auth_state_service)],
) -> AccountSecurityActionResponse:
    _set_auth_no_store(response)
    await service.dismiss_mfa_prompt(user_id=user.user_id, session_id=_account_session_id(user))
    return AccountSecurityActionResponse()


@router.post("/mfa/settings/enroll/start", response_model=AccountMfaEnrollmentResponse)
async def start_account_mfa_enrollment(
    payload: PasswordConfirmationRequest,
    request: Request,
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[AuthService, Depends(_support_auth_state_service)],
) -> AccountMfaEnrollmentResponse:
    _set_auth_no_store(response)
    challenge_token, setup = await service.start_account_mfa_enrollment(
        user_id=user.user_id,
        session_id=_account_session_id(user),
        password=payload.password,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return AccountMfaEnrollmentResponse(
        challenge_token=challenge_token,
        secret=setup.secret,
        provisioning_uri=setup.provisioning_uri,
        recovery_codes=setup.recovery_codes,
        expires_in=setup.expires_in,
    )


@router.post("/mfa/settings/enroll/confirm", response_model=TokenResponse)
async def complete_account_mfa_enrollment(
    payload: MfaCodeRequest,
    request: Request,
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[AuthService, Depends(_support_auth_state_service)],
) -> TokenResponse:
    _set_auth_no_store(response)
    device_id = _device_id_from_request(request)
    result = await service.complete_account_mfa_enrollment(
        user_id=user.user_id,
        session_id=_account_session_id(user),
        challenge_token=payload.challenge_token,
        code=payload.code,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        device_id=device_id,
    )
    _set_device_cookie(response, device_id)
    _set_refresh_cookie(response, result.refresh_token)
    return TokenResponse(access_token=result.access_token, expires_in=result.expires_in)


@router.post("/mfa/settings/disable", response_model=TokenResponse)
async def disable_account_mfa(
    payload: AccountMfaDisableRequest,
    request: Request,
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[AuthService, Depends(_support_auth_state_service)],
) -> TokenResponse:
    _set_auth_no_store(response)
    device_id = _device_id_from_request(request)
    result = await service.disable_account_mfa(
        user_id=user.user_id,
        session_id=_account_session_id(user),
        password=payload.password,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        device_id=device_id,
    )
    _set_device_cookie(response, device_id)
    _set_refresh_cookie(response, result.refresh_token)
    return TokenResponse(access_token=result.access_token, expires_in=result.expires_in)


@router.post(
    "/password/setup/code", response_model=LoginCodeResponse, status_code=status.HTTP_202_ACCEPTED
)
async def request_account_password_setup_code(
    request: Request,
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[IdentityService, Depends(_identity_service)],
    payload: Annotated[SetupChannelRequest | None, Body()] = None,
) -> LoginCodeResponse:
    _set_auth_no_store(response)
    code = await service.request_setup(
        user_id=user.user_id,
        session_id=_account_session_id(user),
        channel=payload.channel if payload else "email",
        ip=_client_ip(request),
        agent=request.headers.get("user-agent"),
    )
    return LoginCodeResponse(dev_code=code)


@router.post("/password/setup", response_model=AccountSecurityActionResponse)
async def setup_account_password(
    payload: PasswordSetupRequest,
    request: Request,
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[IdentityService, Depends(_identity_service)],
) -> AccountSecurityActionResponse:
    _set_auth_no_store(response)
    identity = await service.identity_settings(user.user_id, _account_session_id(user))
    if identity["has_password"]:
        raise ConflictError("Account password is already configured")
    contact = identity["email"] if payload.channel == "email" else identity["phone"]
    if not contact:
        raise AuthenticationError("Verified contact unavailable")
    await service.change_password(
        channel=payload.channel,
        contact=str(contact),
        purpose="setup",
        session_id=_account_session_id(user),
        code=payload.code,
        new_password=payload.new_password,
        ip=_client_ip(request),
    )
    return AccountSecurityActionResponse()


@router.post("/password/confirm", response_model=TokenResponse)
async def confirm_account_password(
    payload: PasswordConfirmationRequest,
    request: Request,
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[AuthService, Depends(_support_auth_state_service)],
) -> TokenResponse:
    _set_auth_no_store(response)
    token = await service.confirm_account_password(
        user_id=user.user_id,
        session_id=_account_session_id(user),
        password=payload.password,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return TokenResponse(access_token=token, expires_in=get_settings().ACCESS_TOKEN_MINUTES * 60)


@router.post(
    "/mfa/enroll/start",
    response_model=MfaEnrollmentStartResponse,
)
async def start_mfa_enrollment(
    payload: MfaChallengeRequest,
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(_support_auth_state_service)],
) -> MfaEnrollmentStartResponse:
    _set_auth_no_store(response)
    setup = await service.start_mfa_enrollment(
        challenge_token=payload.challenge_token,
        ip_address=_client_ip(request),
    )
    return MfaEnrollmentStartResponse(
        secret=setup.secret,
        provisioning_uri=setup.provisioning_uri,
        recovery_codes=setup.recovery_codes,
        expires_in=setup.expires_in,
    )


@router.post("/mfa/enroll/confirm", response_model=TokenResponse)
async def complete_mfa_enrollment(
    payload: MfaCodeRequest,
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(_support_auth_state_service)],
) -> TokenResponse:
    _set_auth_no_store(response)
    device_id = _device_id_from_request(request)
    result = await service.complete_mfa_enrollment(
        challenge_token=payload.challenge_token,
        code=payload.code,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        device_id=device_id,
    )
    _set_device_cookie(response, device_id)
    _set_refresh_cookie(response, result.refresh_token)
    return TokenResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
    )


@router.post("/mfa/verify", response_model=TokenResponse)
async def verify_mfa(
    payload: MfaCodeRequest,
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(_support_auth_state_service)],
) -> TokenResponse:
    _set_auth_no_store(response)
    device_id = _device_id_from_request(request)
    result = await service.verify_mfa(
        challenge_token=payload.challenge_token,
        code=payload.code,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        device_id=device_id,
    )
    _set_device_cookie(response, device_id)
    _set_refresh_cookie(response, result.refresh_token)
    return TokenResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
    )


@router.post("/mfa/recover", response_model=MfaChallengeResponse)
async def recover_mfa(
    payload: MfaRecoveryRequest,
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(_support_auth_state_service)],
) -> MfaChallengeResponse:
    _set_auth_no_store(response)
    result = await service.recover_mfa(
        challenge_token=payload.challenge_token,
        recovery_code=payload.recovery_code,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return MfaChallengeResponse(
        status="mfa_enrollment_required",
        challenge_token=result.challenge_token,
        expires_in=result.expires_in,
    )


@router.post("/mfa/step-up", response_model=TokenResponse)
async def step_up_mfa(
    payload: MfaStepUpRequest,
    request: Request,
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[AuthService, Depends(_support_auth_state_service)],
) -> TokenResponse:
    _set_auth_no_store(response)
    if user.session_id is None:
        raise AuthenticationError("Authenticated session is missing")
    access_token, expires_in = await service.step_up_mfa(
        user_id=user.user_id,
        session_id=user.session_id,
        is_developer=user.is_developer,
        is_administrator=user.is_administrator,
        code=payload.code,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return TokenResponse(
        access_token=access_token,
        expires_in=expires_in,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(_auth_state_service)],
    payload: Annotated[RefreshRequest | None, Body()] = None,
) -> TokenResponse:
    _set_auth_no_store(response)
    _assert_cookie_refresh_origin(request)
    refresh_token = _refresh_token_from_request(request)
    access, refresh, expires = await service.refresh(
        refresh_token=refresh_token,
        operation_id=payload.operation_id if payload and payload.operation_id else uuid4(),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _set_refresh_cookie(response, refresh)
    return TokenResponse(access_token=access, expires_in=expires)


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(_auth_state_service)],
    payload: Annotated[LogoutRequest | None, Body()] = None,
) -> LogoutResponse:
    _set_auth_no_store(response)
    _assert_cookie_refresh_origin(request)
    try:
        refresh_token = _refresh_token_from_request(request)
    except AuthenticationError:
        refresh_token = None
    if refresh_token:
        await service.logout(
            refresh_token,
            operation_id=payload.operation_id if payload is not None else None,
        )
    _clear_refresh_cookie(response)
    return LogoutResponse()


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[AuthService, Depends(_auth_state_service)],
) -> SessionListResponse:
    _set_auth_no_store(response)
    sessions = await service.list_sessions(
        user_id=user.user_id,
        current_session_id=user.session_id,
    )
    return SessionListResponse(
        items=[ActiveSessionResponse.model_validate(session) for session in sessions]
    )


@router.post("/sessions/revoke-others", response_model=SessionRevokeResponse)
async def revoke_other_sessions(
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[AuthService, Depends(_auth_state_service)],
) -> SessionRevokeResponse:
    _set_auth_no_store(response)
    revoked_count = await service.revoke_other_sessions(
        user_id=user.user_id,
        current_session_id=user.session_id,
    )
    return SessionRevokeResponse(revoked_count=revoked_count)


@router.delete("/sessions/{session_id}", response_model=SessionRevokeResponse)
async def revoke_session(
    session_id: UUID,
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[AuthService, Depends(_auth_state_service)],
) -> SessionRevokeResponse:
    _set_auth_no_store(response)
    await service.revoke_session(
        user_id=user.user_id,
        session_id=session_id,
        current_session_id=user.session_id,
    )
    return SessionRevokeResponse(revoked_count=1)


@router.get("/profile", response_model=ProfileResponse)
async def profile(
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> ProfileResponse:
    _set_auth_no_store(response)
    service = ProfileService(ProfileRepository(db), AuthRepository(db))
    return await service.get_profile(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        branch_assignments=user.branch_assignments,
        is_tenant_owner=user.is_tenant_owner,
        is_support_access=user.support_access_session_id is not None,
    )


@router.get("/me", response_model=MeResponse)
async def me(
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[AuthService, Depends(_service)],
) -> MeResponse:
    _set_auth_no_store(response)
    info = await service.get_user_info(user.user_id)
    me_data = MeResponse.model_validate(info)
    me_data.level = user.level
    me_data.active_tenant_id = user.tenant_id
    me_data.is_tenant_owner = user.is_tenant_owner
    me_data.branch_assignments = user.branch_assignments
    me_data.permissions = sorted(user.permissions)
    me_data.permission_scopes = {
        code: None if scope is None else sorted(scope, key=str)
        for code, scope in sorted(user.permission_scopes.items())
    }
    me_data.platform_capabilities = sorted(user.platform_capabilities)
    if (
        user.support_access_session_id is not None
        and user.tenant_id is not None
        and user.support_access_tenant_name is not None
        and user.support_access_reason is not None
        and user.support_access_expires_at is not None
        and user.support_access_is_read_only is not None
    ):
        me_data.support_access = SupportAccessContextResponse(
            id=user.support_access_session_id,
            tenant_id=user.tenant_id,
            tenant_name=user.support_access_tenant_name,
            reason=user.support_access_reason,
            capabilities=sorted(user.permissions),
            is_read_only=user.support_access_is_read_only,
            expires_at=user.support_access_expires_at,
        )
    return me_data


@router.get("/preferences", response_model=UserPreferencesRead)
async def get_preferences(
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[AuthService, Depends(_service)],
) -> UserPreferencesRead:
    _set_auth_no_store(response)
    preferences = await service.get_user_preferences(user.user_id)
    return _preferences_response(preferences, user)


@router.patch("/preferences", response_model=UserPreferencesRead)
async def update_preferences(
    payload: UserPreferencesUpdate,
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[AuthService, Depends(_service)],
) -> UserPreferencesRead:
    _set_auth_no_store(response)
    raw = payload.model_dump(exclude_none=True)
    expected_version = int(raw.pop("expected_version"))
    workspace_value = raw.pop("workspace", None)
    workspace = dict(workspace_value) if isinstance(workspace_value, dict) else None
    preferences = await service.update_user_preferences(
        user_id=user.user_id,
        workspace_scope=_workspace_scope(user),
        expected_version=expected_version,
        fields=raw,
        workspace=workspace,
    )
    return _preferences_response(preferences, user)
