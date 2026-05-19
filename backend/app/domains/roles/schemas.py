"""Pydantic schemas for the roles domain."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class PermissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    group_code: str
    name: str
    description: str | None
    min_level_required: int
    is_dangerous: bool
    is_active: bool


class RoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None
    name: str
    description: str | None
    level: int
    is_system: bool
    is_active: bool


class RoleWithPermissions(RoleRead):
    permissions: list[str]


class AssignmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    tenant_id: UUID
    branch_id: UUID | None
    role_id: UUID
    password_required: bool
    is_active: bool


class UserInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str
    phone: str | None
    status: str
    last_login_at: datetime | None


class UserWithAssignments(UserInfo):
    assignments: list[AssignmentRead]


class InviteUserRequest(BaseModel):
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
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = None
