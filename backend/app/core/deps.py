"""FastAPI dependencies.

`get_db` picks the app or support pool based on the auth context populated by
`AuthContextMiddleware`, opens a transaction, and seeds the RLS GUCs.

`current_user` decodes the access token and assembles a CurrentUser snapshot
that includes the effective permission set recomputed from the database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AppSessionLocal, SupportSessionLocal
from app.core.errors import (
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from app.core.redis import redis_client
from app.core.security import decode_access_token


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield an RLS-scoped transaction that must finish before the response.

    Always inject this dependency with ``scope="function"`` so the transaction
    commits before a client can issue a follow-up request.
    """
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


async def get_auth_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Commit intentional auth-state changes even when login is rejected.

    Failed-code and lockout rows are part of the security ledger. The regular
    request transaction correctly rolls back on domain errors, so auth routes
    use this narrower dependency and commit only expected authentication
    outcomes. Unexpected failures still roll back.
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    user_id = getattr(request.state, "user_id", None)
    is_support = bool(getattr(request.state, "is_support_session", False))
    use_support_pool = bool(getattr(request.state, "use_support_pool", False))

    sessionmaker = SupportSessionLocal if use_support_pool else AppSessionLocal

    async with sessionmaker() as session:
        transaction = await session.begin()
        try:
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
        except (AuthenticationError, RateLimitError, NotFoundError):
            if transaction.is_active:
                await transaction.commit()
            raise
        except BaseException:
            if transaction.is_active:
                await transaction.rollback()
            raise
        else:
            if transaction.is_active:
                await transaction.commit()


async def get_redis() -> Redis:
    return redis_client


def _required_uuid_claim(claims: Mapping[str, object], key: str) -> UUID:
    raw = claims.get(key)
    if not isinstance(raw, str) or not raw:
        raise AuthenticationError(f"Token missing {key}")
    try:
        return UUID(raw)
    except ValueError as exc:
        raise AuthenticationError(f"Token {key} is not a valid UUID") from exc


def _optional_uuid_claim(claims: Mapping[str, object], key: str) -> UUID | None:
    raw = claims.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise AuthenticationError(f"Token {key} is not a valid UUID")
    try:
        return UUID(raw)
    except ValueError as exc:
        raise AuthenticationError(f"Token {key} is not a valid UUID") from exc


@dataclass
class CurrentUser:
    user_id: UUID
    tenant_id: UUID | None
    is_developer: bool
    is_administrator: bool
    permissions: set[str] = field(default_factory=set)
    permission_scopes: dict[str, frozenset[UUID] | None] = field(default_factory=dict)
    branch_assignments: dict[str, str] = field(default_factory=dict)
    assignment_levels: dict[str, int] = field(default_factory=dict)
    policy_revision: int | None = None
    subject_revision: int | None = None
    membership_status: str | None = None
    is_tenant_owner: bool = False

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
        if self.assignment_levels:
            return min(self.assignment_levels.values())
        return 4

    @property
    def assigned_branch_ids(self) -> set[UUID]:
        branch_ids: set[UUID] = set()
        for key in self.branch_assignments:
            if key == "tenant":
                continue
            try:
                branch_ids.add(UUID(key))
            except ValueError:
                continue
        return branch_ids

    def branch_scope_for(self, permission_code: str) -> set[UUID] | None:
        """Return only the branches paired with this capability.

        ``None`` means the capability was granted by a tenant-wide assignment;
        an empty set means it is absent or has no usable branch scope.
        """
        if self.is_developer:
            return None
        if permission_code not in self.permissions:
            return set()
        scope = self.permission_scopes.get(permission_code, frozenset())
        return None if scope is None else set(scope)

    def branch_scope_for_any(self, *permission_codes: str) -> set[UUID] | None:
        if self.is_developer:
            return None
        combined: set[UUID] = set()
        for code in permission_codes:
            if code not in self.permissions:
                continue
            scope = self.permission_scopes.get(code, frozenset())
            if scope is None:
                return None
            combined.update(scope)
        return combined

    def branch_scope_for_all(self, *permission_codes: str) -> set[UUID] | None:
        """Intersect scopes when an operation requires multiple capabilities."""
        if self.is_developer:
            return None
        combined: set[UUID] | None = None
        for code in permission_codes:
            scope = self.branch_scope_for(code)
            if scope is None:
                continue
            combined = set(scope) if combined is None else combined.intersection(scope)
        return combined

    def has_tenant_scope(self, permission_code: str) -> bool:
        return self.is_developer or (
            permission_code in self.permissions
            and self.permission_scopes.get(permission_code, frozenset()) is None
        )

    def can_access_branch(self, permission_code: str, branch_id: UUID) -> bool:
        branch_scope = self.branch_scope_for(permission_code)
        return branch_scope is None or branch_id in branch_scope

    def can_access_branch_for_any(self, branch_id: UUID, *permission_codes: str) -> bool:
        branch_scope = self.branch_scope_for_any(*permission_codes)
        return branch_scope is None or branch_id in branch_scope


async def current_user(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    redis: Annotated[Redis, Depends(get_redis)],
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationError("Authentication required")

    token = authorization.split(" ", 1)[1].strip()
    claims = decode_access_token(token)

    user_id = _required_uuid_claim(claims, "sub")
    tenant_id = _optional_uuid_claim(claims, "tenant_id")

    is_dev = bool(claims.get("is_developer", False))
    is_admin = bool(claims.get("is_administrator", False))

    # JWTs are short-lived snapshots, but blocking and support-role removal
    # must take effect immediately. Re-check the global identity before any
    # permission or tenant data is loaded.
    from app.domains.auth.repository import AuthRepository

    identity = await AuthRepository(db).get_user_by_id(user_id)
    if identity is None or identity.status not in ("invited", "active"):
        raise AuthenticationError("User is inactive")
    if identity.is_developer is not is_dev or identity.is_administrator is not is_admin:
        raise AuthenticationError("Session claims are outdated")
    if tenant_id is not None and (
        identity.home_tenant_id != tenant_id or identity.membership_status != "active"
    ):
        raise AuthenticationError("Tenant membership is inactive")

    permissions: set[str] = set()
    permission_scopes: dict[str, frozenset[UUID] | None] = {}
    branch_assignments: dict[str, str] = {}
    assignment_levels: dict[str, int] = {}
    policy_revision: int | None = None
    subject_revision: int | None = None
    membership_status: str | None = None
    is_tenant_owner = False

    if tenant_id is not None:
        # Local import — roles depends on auth at module level, can't import
        # the other way without a circular reference.
        from app.domains.roles.repository import RolesRepository
        from app.domains.roles.service import RolesService

        service = RolesService(RolesRepository(db), redis=redis)
        membership = await service.repo.get_membership_for_user(
            tenant_id=tenant_id,
            user_id=user_id,
        )
        membership_status = membership.status if membership is not None else None
        is_tenant_owner = await service.repo.has_active_ownership(
            tenant_id=tenant_id,
            user_id=user_id,
        )
        authorization_snapshot = await service.get_authorization_snapshot(user_id, tenant_id)
        permissions = set(authorization_snapshot.permissions)
        permission_scopes = dict(authorization_snapshot.permission_scopes)
        policy_revision = authorization_snapshot.policy_revision
        subject_revision = authorization_snapshot.subject_revision

        # branch_assignments: {branch_id_str | "tenant": role_id_str}
        assignments = await service.repo.list_assignments_for_user(user_id, tenant_id=tenant_id)
        active_assignments = [
            a for a in assignments if a.is_active and membership_status == "active"
        ]
        roles_by_id = await service.repo.roles_by_ids([a.role_id for a in active_assignments])
        for a in active_assignments:
            role = roles_by_id.get(a.role_id)
            if role is None or not role.is_active:
                continue
            key = str(a.branch_id) if a.branch_id is not None else "tenant"
            branch_assignments[key] = str(a.role_id)
            assignment_levels[key] = role.level

    return CurrentUser(
        user_id=user_id,
        tenant_id=tenant_id,
        is_developer=is_dev,
        is_administrator=is_admin,
        permissions=permissions,
        permission_scopes=permission_scopes,
        branch_assignments=branch_assignments,
        assignment_levels=assignment_levels,
        policy_revision=policy_revision,
        subject_revision=subject_revision,
        membership_status=membership_status,
        is_tenant_owner=is_tenant_owner,
    )


def require_permission(code: str):  # type: ignore[no-untyped-def]
    """Dependency factory — declares that a route needs `code`.

    Developer retains the temporary phase-one bypass. Administrator access is
    granted only by explicit support dependencies on admin routes.
    """

    async def _checker(
        user: Annotated[CurrentUser, Depends(current_user)],
    ) -> CurrentUser:
        if user.is_developer:
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
        if user.is_developer:
            return user
        if any(code in user.permissions for code in codes):
            return user
        raise PermissionDeniedError(f"Missing one of permissions: {', '.join(codes)}")

    return _checker


def require_tenant_permission(code: str):  # type: ignore[no-untyped-def]
    """Require a capability granted by a tenant-wide assignment."""

    async def _checker(
        user: Annotated[CurrentUser, Depends(current_user)],
    ) -> CurrentUser:
        if not user.is_developer and code not in user.permissions:
            raise PermissionDeniedError(f"Missing permission: {code}")
        if user.has_tenant_scope(code):
            return user
        raise PermissionDeniedError(f"Tenant-wide permission required: {code}")

    return _checker


async def require_writable_tenant(
    user: Annotated[CurrentUser, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
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
