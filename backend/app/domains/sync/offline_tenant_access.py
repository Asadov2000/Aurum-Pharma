"""Trusted tenant policy projection for narrowing offline command candidates."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from app.domains.sync.schemas import OfflinePosCommand
from app.domains.tenant_access.schemas import AccessModule

_TENANT_ADAPTER: TypeAdapter[UUID] = TypeAdapter(UUID4, config=ConfigDict(strict=True))
_COMMANDS_ADAPTER: TypeAdapter[tuple[OfflinePosCommand, ...]] = TypeAdapter(
    tuple[OfflinePosCommand, ...], config=ConfigDict(strict=True)
)
_POS_MUTATIONS: frozenset[OfflinePosCommand] = frozenset(
    {"sale.cash.complete", "shift.close", "shift.open"}
)


def _require_utc(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Tenant access evaluation requires UTC")


class CloudTenantAccessPolicyV1(BaseModel):
    """Current Cloud policy; quotas and administrative notes grant no commands."""

    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )

    tenant_id: UUID4
    version: int = Field(ge=1, le=9_223_372_036_854_775_807)
    tenant_status: Literal[
        "setup", "trial", "active", "grace_period", "readonly", "suspended", "archived"
    ]
    mode: Literal["full", "readonly"]
    blocked_modules: tuple[AccessModule, ...]
    expires_at: datetime | None

    @field_validator("blocked_modules")
    @classmethod
    def validate_modules(cls, value: tuple[AccessModule, ...]) -> tuple[AccessModule, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("Tenant access modules must be unique and sorted")
        return value

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            _require_utc(value)
        return value


def permitted_offline_commands(
    policy: CloudTenantAccessPolicyV1,
    *,
    tenant_id: UUID,
    commands: tuple[OfflinePosCommand, ...],
    now: datetime,
) -> tuple[OfflinePosCommand, ...]:
    """Intersect trusted candidates with policy at the supplied trusted Cloud time.

    Expired policy restrictions no longer apply, but lifecycle denial persists.
    This cannot authorize a command absent from the caller's existing candidates.
    """

    current = CloudTenantAccessPolicyV1.model_validate(policy)
    selected_tenant = _TENANT_ADAPTER.validate_python(tenant_id)
    candidates = _COMMANDS_ADAPTER.validate_python(commands)
    if candidates != tuple(sorted(set(candidates))):
        raise ValueError("Offline command candidates must be unique and sorted")
    _require_utc(now)
    if current.tenant_id != selected_tenant:
        raise ValueError("Tenant access policy scope mismatch")
    if current.tenant_status not in {"active", "trial", "grace_period"}:
        raise ValueError("Tenant lifecycle denies offline access")
    if current.expires_at is not None and now >= current.expires_at:
        return candidates
    if current.mode == "readonly" or "pos" in current.blocked_modules:
        return tuple(command for command in candidates if command not in _POS_MUTATIONS)
    if "inventory" in current.blocked_modules:
        return tuple(command for command in candidates if command != "sale.cash.complete")
    return candidates
