"""Read-only personal profile, separate from tenant personnel records."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ProfileBranch(BaseModel):
    id: UUID
    name: str


class ProfileAssignment(BaseModel):
    role_id: UUID
    role_name: str
    scope: Literal["tenant", "branch"]
    branch: ProfileBranch | None


class ProfileResponse(BaseModel):
    id: UUID
    full_name: str
    email: str
    status: str
    tenant_id: UUID | None
    is_tenant_owner: bool
    platform_role: Literal["developer", "administrator"] | None
    access_assignments: list[ProfileAssignment] = Field(default_factory=list)
