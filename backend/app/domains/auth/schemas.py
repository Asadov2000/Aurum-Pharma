"""Pydantic v2 request / response schemas for the auth endpoints."""

from __future__ import annotations

from datetime import datetime
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
    access_token: str
    # Phase 2 security hardening: refresh tokens are delivered in an httpOnly
    # cookie, not in the JSON body. Kept optional for schema compatibility.
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int  # seconds


class RefreshRequest(BaseModel):
    refresh_token: str | None = None
    operation_id: UUID4 | None = None


class LogoutRequest(BaseModel):
    refresh_token: str | None = None
    operation_id: UUID4 | None = None


class LogoutResponse(BaseModel):
    status: str = "ok"


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
    # role_id keyed by branch_id (or the literal "tenant" for tenant-wide
    # assignments). Empty when the request has no tenant_id in the token.
    branch_assignments: dict[str, str] = {}
    # Effective permission codes (resolved from roles, cached in Redis). Lets the
    # UI hide nav/actions the user can't use. Not in the JWT — sent in the body.
    permissions: list[str] = []
