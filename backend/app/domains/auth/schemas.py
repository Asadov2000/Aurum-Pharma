"""Pydantic v2 request / response schemas for the auth endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict, EmailStr, Field


class LoginCodeRequest(BaseModel):
    email: EmailStr


class LoginCodeResponse(BaseModel):
    """Returned for every code request — successful or not — to avoid leaking
    whether an email exists in the system (anti-enumeration).

    `dev_code` is populated ONLY in `ENVIRONMENT=development`, so the local
    UI can pre-fill the field without making the user grep Celery logs.
    Never set in staging / production responses.
    """

    status: str = "ok"
    dev_code: str | None = None


class LoginCodeVerify(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    password: str | None = None


class TokenResponse(BaseModel):
    status: Literal["authenticated"] = "authenticated"
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class MfaChallengeResponse(BaseModel):
    status: Literal[
        "mfa_required",
        "mfa_enrollment_required",
        "mfa_recovery_required",
    ]
    challenge_token: str
    expires_in: int


class MfaChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_token: str = Field(min_length=64, max_length=128)


class MfaCodeRequest(MfaChallengeRequest):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class MfaEnrollmentStartResponse(BaseModel):
    status: Literal["mfa_enrollment_ready"] = "mfa_enrollment_ready"
    secret: str
    provisioning_uri: str
    recovery_codes: list[str]
    expires_in: int


class MfaRecoveryRequest(MfaChallengeRequest):
    recovery_code: str = Field(min_length=20, max_length=32)


class MfaStepUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID4 | None = None


class LogoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID4 | None = None


class LogoutResponse(BaseModel):
    status: str = "ok"


class ActiveSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_agent: str | None
    ip_address: str | None
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    is_current: bool


class SessionListResponse(BaseModel):
    items: list[ActiveSessionResponse]


class SessionRevokeResponse(BaseModel):
    status: Literal["ok"] = "ok"
    revoked_count: int = Field(ge=0)


class MeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str
    is_developer: bool
    is_administrator: bool
    home_tenant_id: UUID | None
    status: str
    last_login_at: datetime | None
    level: int = 4
    is_tenant_owner: bool = False
    # role_id keyed by branch_id (or the literal "tenant" for tenant-wide
    # assignments). Empty when the request has no tenant_id in the token.
    branch_assignments: dict[str, str] = {}
    # Effective permission codes (resolved from roles, cached in Redis). Lets the
    # UI hide nav/actions the user can't use. Not in the JWT — sent in the body.
    permissions: list[str] = []
