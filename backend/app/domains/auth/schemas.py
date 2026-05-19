"""Pydantic v2 request / response schemas for the auth endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginCodeRequest(BaseModel):
    email: EmailStr


class LoginCodeResponse(BaseModel):
    """Returned for every code request — successful or not — to avoid leaking
    whether an email exists in the system (anti-enumeration)."""

    status: str = "ok"


class LoginCodeVerify(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    password: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


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
    # TODO(roles): add branch_assignments when migration 0004 lands
