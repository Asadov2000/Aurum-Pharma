"""Pydantic schemas for the roles domain."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class PermissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    group_code: str
    name: str
    description: str | None
    is_dangerous: bool
    is_active: bool
    scope_type: Literal["PLATFORM", "TENANT_ALL", "BRANCH_SET", "OWN"]
    target_role_type: Literal["platform", "tenant"]
    risk_level: Literal["normal", "sensitive", "critical"]
    requires_step_up: bool
    requires_confirmation: bool


class RoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None
    name: str
    description: str | None
    is_system: bool
    is_active: bool
    is_protected: bool
    protected_kind: str | None
    version: int


class RoleWithPermissions(RoleRead):
    permissions: list[str]
    has_hidden_permissions: bool = False
    active_assignment_count: int = 0


class RoleCreate(BaseModel):
    """Create a custom tenant role from the server-provided catalogue."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    """Patch a custom tenant role; protected roles are never editable."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] | None = None


class RoleVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role_id: UUID
    version: int
    name: str
    description: str | None
    status: Literal["draft", "published", "archived"]
    permissions: list[str]
    published_at: datetime | None
    archived_at: datetime | None
    created_at: datetime
    created_by: UUID | None
    created_by_name: str | None


class RoleArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    replacement_role_id: UUID


class RoleArchiveResponse(BaseModel):
    archived_version: int
    affected_memberships: int


class TemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    description: str | None
    is_system: bool
    is_active: bool


class TemplateWithPermissions(TemplateRead):
    """A role preset: name + description + recommended permission set. Only a
    hint for the builder — it grants nothing on its own."""

    permissions: list[str]


class AssignmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    tenant_id: UUID
    membership_id: UUID
    branch_id: UUID | None
    role_id: UUID
    role_version_id: UUID
    role_name: str | None = None
    password_required: bool
    is_active: bool


class UserInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    membership_id: UUID
    is_tenant_owner: bool
    email: str
    full_name: str
    phone: str | None
    status: str
    last_login_at: datetime | None
    can_require_password: bool


class UserWithAssignments(UserInfo):
    assignments: list[AssignmentRead]


class UserListResponse(BaseModel):
    items: list[UserWithAssignments]
    total: int
    page: int
    page_size: int


class UserSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str | None = Field(default=None, max_length=200)
    status: Literal["pending", "active", "suspended", "offboarded"] | None = None
    role_id: UUID | None = None
    branch_id: UUID | None = None
    page: int = Field(default=1, ge=1, strict=True)
    page_size: int = Field(default=50, ge=1, le=200, strict=True)

    @field_validator("q")
    @classmethod
    def _normalize_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class UserSessionRevokeResponse(BaseModel):
    status: Literal["ok"] = "ok"
    revoked_count: int = Field(ge=0)


class InviteUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    role_id: UUID
    branch_id: UUID | None = None
    password_required: bool = False


class AssignmentCreate(BaseModel):
    role_id: UUID
    branch_id: UUID | None = None
    password_required: bool = False


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = None
    status: Literal["active"] | None = None


class TenantAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    phone: str | None = None


class TenantMembershipRead(BaseModel):
    membership_id: UUID
    user_id: UUID
    tenant_id: UUID
    email: str
    full_name: str
    phone: str | None
    status: Literal["pending", "active", "suspended", "offboarded"]


class OwnershipTransferCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
    target_membership_id: UUID


class OwnershipTransferRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    initiator_membership_id: UUID
    initiator_user_id: UUID
    initiator_full_name: str
    target_membership_id: UUID
    target_user_id: UUID
    target_full_name: str
    status: Literal["pending", "completed", "cancelled", "expired"]
    expires_at: datetime
    completed_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime
    updated_at: datetime


class OwnershipTransferListResponse(BaseModel):
    items: list[OwnershipTransferRead]


class OwnershipTransferActionResponse(BaseModel):
    transfer: OwnershipTransferRead
    sessions_revoked: bool = False
