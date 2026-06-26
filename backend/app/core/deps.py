"""FastAPI dependencies.

`get_db` picks the app or support pool based on the auth context populated by
`AuthContextMiddleware`, opens a transaction, and seeds the RLS GUCs.

`current_user` decodes the access token and assembles a CurrentUser snapshot
that includes the effective permission set (loaded from Redis cache, or
recomputed from DB and re-cached).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AppSessionLocal, SupportSessionLocal
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.redis import redis_client
from app.core.security import decode_access_token


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    tenant_id = getattr(request.state, "tenant_id", None)
    user_id = getattr(request.state, "user_id", None)
    is_support = bool(getattr(request.state, "is_support_session", False))
    use_support_pool = bool(getattr(request.state, "use_support_pool", False))

    sessionmaker = SupportSessionLocal if use_support_pool else AppSessionLocal

    async with sessionmaker() as session:
        async with session.begin():
            if tenant_id is not None:
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :v, true)"),
                    {"v": str(tenant_id)},
                )
            if user_id is not None:
                await session.execute(
                    text("SELECT set_config('app.user_id', :v, true)"),
                    {"v": str(user_id)},
                )
            if is_support:
                await session.execute(
                    text("SELECT set_config('app.support_session', 'true', true)"),
                )
            yield session


async def get_redis() -> Redis:
    return redis_client


@dataclass
class CurrentUser:
    user_id: UUID
    tenant_id: UUID | None
    is_developer: bool
    is_administrator: bool
    permissions: set[str] = field(default_factory=set)
    branch_assignments: dict[str, str] = field(default_factory=dict)

    @property
    def level(self) -> int:
        """Effective level used for the anti-escalation rule.

        1 = developer, 2 = administrator, 3 = owner, 4 = seller.
        Without any assignment we conservatively treat the user as seller.
        """
        if self.is_developer:
            return 1
        if self.is_administrator:
            return 2
        if "users.invite" in self.permissions or "roles.assign" in self.permissions:
            return 3
        return 4


async def current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationError("Authentication required")

    token = authorization.split(" ", 1)[1].strip()
    claims = decode_access_token(token)

    sub = claims.get("sub")
    if not sub:
        raise AuthenticationError("Token missing subject")
    try:
        user_id = UUID(sub)
    except (TypeError, ValueError) as exc:
        raise AuthenticationError("Token subject is not a valid UUID") from exc

    tenant_raw = claims.get("tenant_id")
    tenant_id: UUID | None
    if tenant_raw:
        try:
            tenant_id = UUID(tenant_raw)
        except (TypeError, ValueError) as exc:
            raise AuthenticationError("Token tenant_id is not a valid UUID") from exc
    else:
        tenant_id = None

    is_dev = bool(claims.get("is_developer", False))
    is_admin = bool(claims.get("is_administrator", False))

    permissions: set[str] = set()
    branch_assignments: dict[str, str] = {}

    if tenant_id is not None:
        # Local import — roles depends on auth at module level, can't import
        # the other way without a circular reference.
        from app.domains.roles.repository import RolesRepository
        from app.domains.roles.service import RolesService

        service = RolesService(RolesRepository(db), redis=redis)
        permissions = await service.get_effective_permissions(user_id, tenant_id)

        # branch_assignments: {branch_id_str | "tenant": role_id_str}
        assignments = await service.repo.list_assignments_for_user(user_id, tenant_id=tenant_id)
        for a in assignments:
            if not a.is_active:
                continue
            key = str(a.branch_id) if a.branch_id is not None else "tenant"
            branch_assignments[key] = str(a.role_id)

    return CurrentUser(
        user_id=user_id,
        tenant_id=tenant_id,
        is_developer=is_dev,
        is_administrator=is_admin,
        permissions=permissions,
        branch_assignments=branch_assignments,
    )


def require_permission(code: str):  # type: ignore[no-untyped-def]
    """Dependency factory — declares that a route needs `code`.

    Developers and administrators bypass per-permission checks per the spec
    (levels 1-2 see everything). Regular users must have `code` in their
    effective permission set.
    """

    async def _checker(
        user: Annotated[CurrentUser, Depends(current_user)],
    ) -> CurrentUser:
        if user.is_developer or user.is_administrator:
            return user
        if code in user.permissions:
            return user
        raise PermissionDeniedError(f"Missing permission: {code}")

    return _checker


def require_any_permission(*codes: str):  # type: ignore[no-untyped-def]
    """Dependency factory for routes that allow several equivalent permissions."""

    async def _checker(
        user: Annotated[CurrentUser, Depends(current_user)],
    ) -> CurrentUser:
        if user.is_developer or user.is_administrator:
            return user
        if any(code in user.permissions for code in codes):
            return user
        raise PermissionDeniedError(f"Missing one of permissions: {', '.join(codes)}")

    return _checker


async def require_writable_tenant(
    user: Annotated[CurrentUser, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CurrentUser:
    """Block mutating endpoints when the tenant is read-only.

    Used in POS endpoints — once billing transitions a tenant to
    'readonly' (via process_grace_endings), the cashier UI must refuse
    to open shifts, sell, refund, etc.
    """
    if user.tenant_id is None:
        return user
    from app.core.errors import BusinessRuleError
    from app.domains.foundation.models import Tenant

    tenant = await db.get(Tenant, user.tenant_id)
    if tenant is not None and tenant.status in (
        "readonly",
        "suspended",
        "archived",
    ):
        raise BusinessRuleError(
            "Tenant is read-only (subscription suspended)",
            details={"status": tenant.status},
        )
    return user
