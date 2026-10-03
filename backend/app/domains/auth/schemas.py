"""Pydantic v2 request / response schemas for the auth endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, Self, TypeVar
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


class AccountMfaSettingsResponse(BaseModel):
    enabled: bool
    prompt_pending: bool
    has_password: bool


class AccountSecurityActionResponse(BaseModel):
    status: Literal["ok"] = "ok"


class PasswordConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=1024)


class PasswordSetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    new_password: str = Field(min_length=12, max_length=128)
    channel: Literal["email", "phone"] = "email"

    @field_validator("new_password")
    @classmethod
    def _bcrypt_input_limit(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Password cannot contain a NUL character")
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must fit within 72 UTF-8 bytes")
        return value


class AccountMfaEnrollmentResponse(MfaEnrollmentStartResponse):
    challenge_token: str


class AccountMfaDisableRequest(PasswordConfirmationRequest):
    """Confirm disabling MFA with the password of the signed-in account."""


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
    # ``null`` means tenant-wide access; a list contains the exact branches where
    # a BRANCH_SET permission is effective. The UI uses this only to hide actions;
    # the backend remains the authorization authority.
    permission_scopes: dict[str, list[UUID] | None] = Field(default_factory=dict)
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


QuickActionId = TypeVar("QuickActionId", bound=str)
CatalogQuickActionId = Literal["open", "edit", "copy_name", "copy_barcode", "archive", "restore"]
BatchQuickActionId = Literal["open", "copy_number"]
IncomingQuickActionId = Literal["open", "copy_number"]
ReceiptQuickActionId = Literal["open", "copy_number"]
EmployeeQuickActionId = Literal[
    "open",
    "profile",
    "assign_access",
    "reissue_invitation",
    "activate",
    "revoke_sessions",
    "suspend",
    "offboard",
    "transfer_ownership",
]


def _catalog_quick_action_order() -> list[CatalogQuickActionId]:
    return ["open", "edit", "copy_name", "copy_barcode", "archive", "restore"]


def _batch_quick_action_order() -> list[BatchQuickActionId]:
    return ["open", "copy_number"]


def _incoming_quick_action_order() -> list[IncomingQuickActionId]:
    return ["open", "copy_number"]


def _receipt_quick_action_order() -> list[ReceiptQuickActionId]:
    return ["open", "copy_number"]


def _employee_quick_action_order() -> list[EmployeeQuickActionId]:
    return [
        "open",
        "profile",
        "assign_access",
        "reissue_invitation",
        "activate",
        "revoke_sessions",
        "suspend",
        "offboard",
        "transfer_ownership",
    ]


class QuickActionsConfig(BaseModel, Generic[QuickActionId]):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, strict=True)
    order: list[QuickActionId] = Field(min_length=1, max_length=9)
    hidden: list[QuickActionId] = Field(default_factory=list, max_length=9)

    @model_validator(mode="after")
    def _validate_actions(self) -> Self:
        if len(self.order) != len(set(self.order)) or len(self.hidden) != len(set(self.hidden)):
            raise ValueError("quick actions must be unique")
        if not set(self.hidden).issubset(self.order):
            raise ValueError("hidden quick actions must be included in order")
        if not set(self.order).difference(self.hidden):
            raise ValueError("at least one quick action must be visible")
        return self


class CatalogQuickActionsConfig(QuickActionsConfig[CatalogQuickActionId]):
    order: list[CatalogQuickActionId] = Field(
        default_factory=_catalog_quick_action_order,
        min_length=1,
        max_length=6,
    )
    hidden: list[CatalogQuickActionId] = Field(default_factory=list, max_length=6)


class BatchQuickActionsConfig(QuickActionsConfig[BatchQuickActionId]):
    order: list[BatchQuickActionId] = Field(
        default_factory=_batch_quick_action_order, min_length=1, max_length=2
    )
    hidden: list[BatchQuickActionId] = Field(default_factory=list, max_length=2)


class IncomingQuickActionsConfig(QuickActionsConfig[IncomingQuickActionId]):
    order: list[IncomingQuickActionId] = Field(
        default_factory=_incoming_quick_action_order, min_length=1, max_length=2
    )
    hidden: list[IncomingQuickActionId] = Field(default_factory=list, max_length=2)


class EmployeeQuickActionsConfig(QuickActionsConfig[EmployeeQuickActionId]):
    order: list[EmployeeQuickActionId] = Field(
        default_factory=_employee_quick_action_order, min_length=1, max_length=9
    )


class ReceiptQuickActionsConfig(QuickActionsConfig[ReceiptQuickActionId]):
    order: list[ReceiptQuickActionId] = Field(
        default_factory=_receipt_quick_action_order, min_length=1, max_length=2
    )
    hidden: list[ReceiptQuickActionId] = Field(default_factory=list, max_length=2)


class QuickActionsPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog: CatalogQuickActionsConfig = Field(default_factory=CatalogQuickActionsConfig)
    batches: BatchQuickActionsConfig = Field(default_factory=BatchQuickActionsConfig)
    incoming: IncomingQuickActionsConfig = Field(default_factory=IncomingQuickActionsConfig)
    employees: EmployeeQuickActionsConfig = Field(default_factory=EmployeeQuickActionsConfig)
    receipts: ReceiptQuickActionsConfig = Field(default_factory=ReceiptQuickActionsConfig)


class QuickActionsPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog: CatalogQuickActionsConfig | None = None
    batches: BatchQuickActionsConfig | None = None
    incoming: IncomingQuickActionsConfig | None = None
    employees: EmployeeQuickActionsConfig | None = None
    receipts: ReceiptQuickActionsConfig | None = None

    @model_validator(mode="after")
    def _require_section(self) -> Self:
        if "receipts" in self.model_fields_set and self.receipts is None:
            raise ValueError("receipts quick actions cannot be null")
        if not self.model_dump(exclude_none=True):
            raise ValueError("at least one quick action section must be provided")
        return self


class UserPreferencesRead(BaseModel):
    theme: Literal["system", "light", "dark"]
    density: Literal["auto", "compact", "comfortable", "touch"]
    contrast: Literal["standard", "high"]
    reduce_motion: bool
    accent: Literal["teal", "blue", "violet", "green", "amber", "rose"]
    workspace: WorkspacePreferences
    quick_actions: QuickActionsPreferences
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
    quick_actions: QuickActionsPreferencesUpdate | None = None

    @model_validator(mode="after")
    def _require_change(self) -> UserPreferencesUpdate:
        changed = self.model_dump(exclude={"expected_version"}, exclude_none=True)
        if not changed:
            raise ValueError("at least one preference must be provided")
        return self
