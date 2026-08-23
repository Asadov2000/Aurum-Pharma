"""Pydantic v2 request / response schemas for the auth endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    UUID4,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

_ALLOWED_WORKSPACE_ROUTES = {
    "/",
    "/admin",
    "/admin/access",
    "/admin/accounts",
    "/admin/sync",
    "/admin/billing",
    "/admin/tenants",
    "/onboarding",
    "/branches",
    "/registers",
    "/users",
    "/roles",
    "/catalog",
    "/batches",
    "/suppliers",
    "/incoming",
    "/pos",
    "/sales",
    "/billing",
    "/reports",
    "/audit",
    "/notifications",
    "/security",
    "/settings",
}


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


class SupportAccessContextResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    tenant_name: str
    reason: str
    capabilities: list[str]
    is_read_only: bool
    expires_at: datetime


class MeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str
    is_developer: bool
    is_administrator: bool
    home_tenant_id: UUID | None
    active_tenant_id: UUID | None = None
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
    # Global control-plane capabilities stay separate from tenant permissions.
    platform_capabilities: list[str] = []
    support_access: SupportAccessContextResponse | None = None


class WorkspacePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    desktop_mode: Literal["auto", "compact", "expanded"] = "auto"
    hidden_routes: list[str] = Field(default_factory=list, max_length=64)
    favorite_routes: list[str] = Field(default_factory=list, max_length=64)
    route_order: list[str] = Field(default_factory=list, max_length=64)
    start_route: str = "/"

    @field_validator("hidden_routes", "favorite_routes", "route_order")
    @classmethod
    def _validate_routes(cls, routes: list[str]) -> list[str]:
        if len(routes) != len(set(routes)):
            raise ValueError("workspace routes must be unique")
        if any(route not in _ALLOWED_WORKSPACE_ROUTES for route in routes):
            raise ValueError("workspace contains an unknown route")
        return routes

    @field_validator("start_route")
    @classmethod
    def _validate_start_route(cls, route: str) -> str:
        if route not in _ALLOWED_WORKSPACE_ROUTES:
            raise ValueError("start_route is unknown")
        return route

    @model_validator(mode="after")
    def _favorite_routes_must_be_visible(self) -> WorkspacePreferences:
        if set(self.favorite_routes).intersection(self.hidden_routes):
            raise ValueError("favorite routes cannot be hidden")
        return self


class UserPreferencesRead(BaseModel):
    theme: Literal["system", "light", "dark"]
    density: Literal["auto", "compact", "comfortable", "touch"]
    contrast: Literal["standard", "high"]
    reduce_motion: bool
    accent: Literal["teal", "blue", "violet", "green", "amber", "rose"]
    workspace: WorkspacePreferences
    version: int = Field(ge=1)
    updated_at: datetime


class UserPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    theme: Literal["system", "light", "dark"] | None = None
    density: Literal["auto", "compact", "comfortable", "touch"] | None = None
    contrast: Literal["standard", "high"] | None = None
    reduce_motion: bool | None = None
    accent: Literal["teal", "blue", "violet", "green", "amber", "rose"] | None = None
    workspace: WorkspacePreferences | None = None

    @model_validator(mode="after")
    def _require_change(self) -> UserPreferencesUpdate:
        changed = self.model_dump(exclude={"expected_version"}, exclude_none=True)
        if not changed:
            raise ValueError("at least one preference must be provided")
        return self
