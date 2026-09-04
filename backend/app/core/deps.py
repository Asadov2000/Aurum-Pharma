"""FastAPI dependencies.

`get_db` picks the app or support pool based on the auth context populated by
`AuthContextMiddleware`, opens a transaction, and seeds the RLS GUCs.

`current_user` decodes the access token and assembles a CurrentUser snapshot
that includes the effective permission set recomputed from the database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID

from fastapi import Depends, Header, Request
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import AppSessionLocal, SupportSessionLocal
from app.core.errors import (
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from app.core.redis import redis_client
from app.core.security import decode_access_token

if TYPE_CHECKING:
    from app.domains.auth.repository import AuthRepository, AuthUserRecord


BranchScopePolicy = Literal["direct", "filter", "resource", "tenant_reference"]

# Runtime route contract for permissions whose database scope_type is BRANCH_SET.
# A database-backed test keeps this list synchronized with permission metadata.
BRANCH_SCOPED_PERMISSIONS = frozenset(
    {
        "branches.view",
        "branches.update",
        "branches.delete",
        "registers.view",
        "registers.create",
        "registers.update",
        "registers.delete",
        "batches.view",
        "batches.view_costs",
        "batches.create",
        "batches.update",
        "batches.write_off",
        "incoming.view",
        "incoming.create",
        "incoming.finalize",
        "incoming.return",
        "pos.shift_open",
        "pos.shift_close",
        "pos.manage_shifts",
        "pos.sell",
        "pos.manage_sales",
        "pos.refund",
        "pos.refund_external_confirm",
        "pos.handle_prescription",
        "customer_returns.view",
        "customer_returns.resolve",
        "reports.view",
        "sales.view.tenant",
    }
)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield an RLS-scoped transaction that must finish before the response.

    Always inject this dependency with ``scope="function"`` so the transaction
    commits before a client can issue a follow-up request.
    """
    await _resolve_support_access_context(request)
    use_support_pool = bool(getattr(request.state, "use_support_pool", False))

    sessionmaker = SupportSessionLocal if use_support_pool else AppSessionLocal

    async with sessionmaker() as session:
        async with session.begin():
            await _seed_request_db_context(request, session)
            yield session


async def get_auth_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Commit intentional auth-state changes even when login is rejected.

    Failed-code and lockout rows are part of the security ledger. The regular
    request transaction correctly rolls back on domain errors, so auth routes
    use this narrower dependency and commit only expected authentication
    outcomes. Unexpected failures still roll back.
    """
    await _resolve_support_access_context(request)
    use_support_pool = bool(getattr(request.state, "use_support_pool", False))

    sessionmaker = SupportSessionLocal if use_support_pool else AppSessionLocal

    async with sessionmaker() as session:
        transaction = await session.begin()
        try:
            await _seed_request_db_context(request, session)
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


async def get_support_auth_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Run support MFA state transitions through the privileged auth boundary.

    Unauthenticated MFA challenge tokens cannot select the support pool via a
    JWT yet. These routes use this dependency explicitly; their repository is
    limited to SECURITY DEFINER auth functions and never receives raw SQL from
    the request.
    """
    async with SupportSessionLocal() as session:
        transaction = await session.begin()
        try:
            await _seed_request_db_context(request, session)
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


async def _resolve_support_access_context(request: Request) -> None:
    """Validate a tenant support session through the privileged pool.

    Tenant business queries still run as ``aurum_app`` and therefore remain
    subject to RLS. The privileged connection is used only for this bounded
    lookup; its session never reaches a domain repository.
    """

    if bool(getattr(request.state, "invalid_support_access", False)):
        raise PermissionDeniedError(
            "Support access session is invalid",
            details={"reason": "support_access_inactive"},
        )
    support_access_session_id = getattr(
        request.state,
        "support_access_session_id",
        None,
    )
    if support_access_session_id is None or bool(
        getattr(request.state, "support_access_resolved", False)
    ):
        return

    user_id = getattr(request.state, "user_id", None)
    auth_session_id = getattr(request.state, "auth_session_id", None)
    if auth_session_id is None:
        raise PermissionDeniedError(
            "Support access is not bound to this authentication session",
            details={"reason": "support_access_inactive"},
        )
    async with SupportSessionLocal() as validation_session:
        async with validation_session.begin():
            row = (
                (
                    await validation_session.execute(
                        text("""
                        WITH RECURSIVE auth_lineage AS (
                          SELECT
                            auth_session.id,
                            auth_session.rotated_from_session_id
                          FROM public.session AS auth_session
                          WHERE auth_session.id = :auth_session_id
                            AND auth_session.user_id = :user_id
                            AND auth_session.revoked_at IS NULL
                            AND auth_session.expires_at > statement_timestamp()

                          UNION ALL

                          SELECT
                            parent.id,
                            parent.rotated_from_session_id
                          FROM public.session AS parent
                          JOIN auth_lineage AS child
                            ON parent.id = child.rotated_from_session_id
                          WHERE parent.user_id = :user_id
                        )
                        SELECT
                          access_session.tenant_id,
                          tenant.name AS tenant_name,
                          access_session.reason,
                          access_session.expires_at,
                          access_session.is_read_only,
                          array_agg(
                            capability.permission_code
                            ORDER BY capability.permission_code
                          ) AS capabilities
                        FROM public.support_access_session AS access_session
                        JOIN public.tenant AS tenant
                          ON tenant.id = access_session.tenant_id
                        JOIN public.app_user AS actor
                          ON actor.id = access_session.actor_user_id
                        JOIN public.support_access_capability AS capability
                          ON capability.support_access_session_id = access_session.id
                         AND capability.tenant_id = access_session.tenant_id
                        WHERE access_session.id = :support_access_session_id
                          AND access_session.actor_user_id = :user_id
                          AND access_session.actor_session_id IN (
                            SELECT auth_lineage.id FROM auth_lineage
                          )
                          AND access_session.revoked_at IS NULL
                          AND access_session.expires_at > statement_timestamp()
                          AND actor.status = 'active'
                          AND (actor.is_developer OR actor.is_administrator)
                        GROUP BY access_session.id, tenant.name
                        """),
                        {
                            "support_access_session_id": support_access_session_id,
                            "user_id": user_id,
                            "auth_session_id": auth_session_id,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )

    if row is None:
        raise PermissionDeniedError(
            "Support access session is expired, revoked, or unavailable",
            details={"reason": "support_access_inactive"},
        )
    request.state.tenant_id = row["tenant_id"]
    request.state.is_support_session = True
    request.state.support_access_capabilities = tuple(row["capabilities"])
    request.state.support_access_reason = row["reason"]
    request.state.support_access_expires_at = row["expires_at"]
    request.state.support_access_tenant_name = row["tenant_name"]
    request.state.support_access_is_read_only = bool(row["is_read_only"])
    request.state.support_access_resolved = True


async def _seed_request_db_context(request: Request, session: AsyncSession) -> None:
    """Bind the validated request identity to the database transaction."""

    await _resolve_support_access_context(request)
    tenant_id = getattr(request.state, "tenant_id", None)
    user_id = getattr(request.state, "user_id", None)
    support_access_session_id = getattr(
        request.state,
        "support_access_session_id",
        None,
    )
    auth_session_id = getattr(request.state, "auth_session_id", None)
    mfa_verified_at = getattr(request.state, "mfa_verified_at", None)
    request_id = getattr(request.state, "request_id", None)
    if request_id is not None:
        await session.execute(
            text("SELECT set_config('app.request_id', :v, true)"),
            {"v": str(request_id)},
        )
    if user_id is not None:
        await session.execute(
            text("SELECT set_config('app.user_id', :v, true)"),
            {"v": str(user_id)},
        )

    if tenant_id is not None:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :v, true)"),
            {"v": str(tenant_id)},
        )
    if support_access_session_id is not None:
        await session.execute(
            text("SELECT set_config('app.support_access_session_id', :v, true)"),
            {"v": str(support_access_session_id)},
        )
    if auth_session_id is not None:
        await session.execute(
            text("SELECT set_config('app.auth_session_id', :v, true)"),
            {"v": str(auth_session_id)},
        )
    if isinstance(mfa_verified_at, int) and not isinstance(mfa_verified_at, bool):
        await session.execute(
            text("SELECT set_config('app.mfa_verified_at', :v, true)"),
            {"v": str(mfa_verified_at)},
        )
    if bool(getattr(request.state, "is_support_session", False)):
        await session.execute(
            text("SELECT set_config('app.support_session', 'true', true)"),
        )


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


def _optional_timestamp_claim(
    claims: Mapping[str, object],
    key: str,
) -> datetime | None:
    raw = claims.get(key)
    if raw is None:
        return None
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise AuthenticationError(f"Token {key} is not a valid timestamp")
    try:
        return datetime.fromtimestamp(raw, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise AuthenticationError(f"Token {key} is not a valid timestamp") from exc


@dataclass
class CurrentUser:
    user_id: UUID
    tenant_id: UUID | None
    is_developer: bool
    is_administrator: bool
    session_id: UUID | None = None
    mfa_verified_at: datetime | None = None
    platform_capabilities: frozenset[str] = field(default_factory=frozenset)
    permissions: set[str] = field(default_factory=set)
    permission_scopes: dict[str, frozenset[UUID] | None] = field(default_factory=dict)
    branch_assignments: dict[str, str] = field(default_factory=dict)
    assignment_levels: dict[str, int] = field(default_factory=dict)
    policy_revision: int | None = None
    subject_revision: int | None = None
    membership_status: str | None = None
    is_tenant_owner: bool = False
    support_access_session_id: UUID | None = None
    support_access_reason: str | None = None
    support_access_expires_at: datetime | None = None
    support_access_tenant_name: str | None = None
    support_access_is_read_only: bool | None = None

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
        if self.is_developer and self.support_access_session_id is None:
            return None
        if permission_code not in self.permissions:
            return set()
        scope = self.permission_scopes.get(permission_code, frozenset())
        return None if scope is None else set(scope)

    def branch_scope_for_any(self, *permission_codes: str) -> set[UUID] | None:
        if self.is_developer and self.support_access_session_id is None:
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
        if self.is_developer and self.support_access_session_id is None:
            return None
        combined: set[UUID] | None = None
        for code in permission_codes:
            scope = self.branch_scope_for(code)
            if scope is None:
                continue
            combined = set(scope) if combined is None else combined.intersection(scope)
        return combined

    def has_tenant_scope(self, permission_code: str) -> bool:
        return (self.is_developer and self.support_access_session_id is None) or (
            permission_code in self.permissions
            and self.permission_scopes.get(permission_code, frozenset()) is None
        )

    def can_access_branch(self, permission_code: str, branch_id: UUID) -> bool:
        branch_scope = self.branch_scope_for(permission_code)
        return branch_scope is None or branch_id in branch_scope

    def can_access_branch_for_any(self, branch_id: UUID, *permission_codes: str) -> bool:
        branch_scope = self.branch_scope_for_any(*permission_codes)
        return branch_scope is None or branch_id in branch_scope


async def _validate_mfa_session(
    *,
    repository: AuthRepository,
    identity: AuthUserRecord,
    user_id: UUID,
    session_id: UUID | None,
    mfa_verified_at: datetime | None,
) -> None:
    if session_id is None or mfa_verified_at is None or identity.mfa_status != "active":
        raise AuthenticationError("Account MFA is required")
    session_mfa_verified_at = await repository.get_session_mfa_verified_at(
        session_id=session_id,
        user_id=user_id,
    )
    # A step-up timestamp is intentionally access-token-only. Persisting it on
    # the refresh session would let a refresh token stolen before step-up inherit
    # the elevated assurance. The claim may therefore be newer than the baseline.
    if session_mfa_verified_at is None or mfa_verified_at > datetime.now(UTC) + timedelta(
        minutes=1
    ):
        raise AuthenticationError("MFA session is inactive")


@dataclass
class _AuthorizationContext:
    permissions: set[str] = field(default_factory=set)
    permission_scopes: dict[str, frozenset[UUID] | None] = field(default_factory=dict)
    branch_assignments: dict[str, str] = field(default_factory=dict)
    assignment_levels: dict[str, int] = field(default_factory=dict)
    policy_revision: int | None = None
    subject_revision: int | None = None
    membership_status: str | None = None
    is_tenant_owner: bool = False


async def _load_authorization_context(
    *,
    request: Request,
    db: AsyncSession,
    redis: Redis,
    user_id: UUID,
    tenant_id: UUID | None,
    support_access_session_id: UUID | None,
) -> _AuthorizationContext:
    context = _AuthorizationContext()
    if support_access_session_id is not None:
        context.permissions = set(getattr(request.state, "support_access_capabilities", ()))
        context.permission_scopes = {code: None for code in context.permissions}
        return context
    if tenant_id is None:
        return context

    # Local imports keep the auth/roles module dependency graph acyclic.
    from app.domains.roles.repository import RolesRepository
    from app.domains.roles.service import RolesService

    service = RolesService(RolesRepository(db), redis=redis)
    membership = await service.repo.get_membership_for_user(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    context.membership_status = membership.status if membership is not None else None
    context.is_tenant_owner = await service.repo.has_active_ownership(
        tenant_id=tenant_id,
        user_id=user_id,
    )
    snapshot = await service.get_authorization_snapshot(user_id, tenant_id)
    context.permissions = set(snapshot.permissions)
    context.permission_scopes = dict(snapshot.permission_scopes)
    context.policy_revision = snapshot.policy_revision
    context.subject_revision = snapshot.subject_revision

    assignments = await service.repo.list_assignments_for_user(user_id, tenant_id=tenant_id)
    assigned_branch_ids = {
        assignment.branch_id
        for assignment in assignments
        if assignment.branch_id is not None and assignment.is_active
    }
    active_branch_ids = await service.repo.active_branch_ids(tenant_id, assigned_branch_ids)
    active = [
        assignment
        for assignment in assignments
        if assignment.is_active
        and context.membership_status == "active"
        and (assignment.branch_id is None or assignment.branch_id in active_branch_ids)
    ]
    roles_by_id = await service.repo.roles_by_ids([assignment.role_id for assignment in active])
    for assignment in active:
        role = roles_by_id.get(assignment.role_id)
        if role is None or not role.is_active:
            continue
        key = str(assignment.branch_id) if assignment.branch_id is not None else "tenant"
        context.branch_assignments[key] = str(assignment.role_id)
        context.assignment_levels[key] = role.level
    return context


async def current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    redis: Annotated[Redis, Depends(get_redis)],
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationError("Authentication required")

    token = authorization.split(" ", 1)[1].strip()
    claims = decode_access_token(token)

    user_id = _required_uuid_claim(claims, "sub")
    token_tenant_id = _optional_uuid_claim(claims, "tenant_id")
    session_id = _optional_uuid_claim(claims, "sid")
    mfa_verified_at = _optional_timestamp_claim(claims, "mfa_at")

    is_dev = bool(claims.get("is_developer", False))
    is_admin = bool(claims.get("is_administrator", False))
    support_access_session_id = getattr(
        request.state,
        "support_access_session_id",
        None,
    )
    tenant_id = (
        getattr(request.state, "tenant_id", None)
        if support_access_session_id is not None
        else token_tenant_id
    )

    # JWTs are short-lived snapshots, but blocking and support-role removal
    # must take effect immediately. Re-check the global identity before any
    # permission or tenant data is loaded.
    from app.domains.auth.repository import AuthRepository

    auth_repo = AuthRepository(db)
    identity = await auth_repo.get_user_by_id(
        user_id,
        session_id=session_id,
    )
    if identity is None or identity.status not in ("invited", "active"):
        raise AuthenticationError("User is inactive")
    if identity.is_developer is not is_dev or identity.is_administrator is not is_admin:
        raise AuthenticationError("Session claims are outdated")
    if is_dev or is_admin:
        await _validate_mfa_session(
            repository=auth_repo,
            identity=identity,
            user_id=user_id,
            session_id=session_id,
            mfa_verified_at=mfa_verified_at,
        )
    if (is_dev or is_admin) and token_tenant_id is not None and support_access_session_id is None:
        raise AuthenticationError("Support tenant access requires a scoped support session")
    if (
        support_access_session_id is None
        and tenant_id is not None
        and (identity.home_tenant_id != tenant_id or identity.membership_status != "active")
    ):
        raise AuthenticationError("Tenant membership is inactive")

    platform_capabilities: frozenset[str] = frozenset()
    if (is_dev or is_admin) and support_access_session_id is None:
        if session_id is None:
            raise AuthenticationError("Support session is inactive")
        platform_capabilities = await auth_repo.get_active_platform_capabilities(
            user_id,
            session_id,
        )
        if not platform_capabilities:
            raise AuthenticationError("Platform access grant is inactive")

    authz = await _load_authorization_context(
        request=request,
        db=db,
        redis=redis,
        user_id=user_id,
        tenant_id=tenant_id,
        support_access_session_id=support_access_session_id,
    )

    return CurrentUser(
        user_id=user_id,
        tenant_id=tenant_id,
        is_developer=is_dev,
        is_administrator=is_admin,
        session_id=session_id,
        mfa_verified_at=mfa_verified_at,
        platform_capabilities=platform_capabilities,
        permissions=authz.permissions,
        permission_scopes=authz.permission_scopes,
        branch_assignments=authz.branch_assignments,
        assignment_levels=authz.assignment_levels,
        policy_revision=authz.policy_revision,
        subject_revision=authz.subject_revision,
        membership_status=authz.membership_status,
        is_tenant_owner=authz.is_tenant_owner,
        support_access_session_id=support_access_session_id,
        support_access_reason=getattr(request.state, "support_access_reason", None),
        support_access_expires_at=getattr(
            request.state,
            "support_access_expires_at",
            None,
        ),
        support_access_tenant_name=getattr(
            request.state,
            "support_access_tenant_name",
            None,
        ),
        support_access_is_read_only=getattr(
            request.state,
            "support_access_is_read_only",
            None,
        ),
    )


def _ensure_recent_mfa(user: CurrentUser) -> None:
    verified_at = user.mfa_verified_at
    if verified_at is None:
        raise PermissionDeniedError(
            "Recent MFA verification required",
            details={"reason": "mfa_step_up_required"},
        )
    now = datetime.now(UTC)
    max_age = timedelta(minutes=get_settings().MFA_STEP_UP_MINUTES)
    if verified_at > now + timedelta(minutes=1) or now - verified_at > max_age:
        raise PermissionDeniedError(
            "Recent MFA verification required",
            details={"reason": "mfa_step_up_required"},
        )


async def require_recent_account_mfa(
    user: Annotated[CurrentUser, Depends(current_user)],
) -> CurrentUser:
    _ensure_recent_mfa(user)
    return user


async def require_recent_support_mfa(
    user: Annotated[CurrentUser, Depends(current_user)],
) -> CurrentUser:
    if not (user.is_developer or user.is_administrator):
        raise PermissionDeniedError("Support privileges required")
    _ensure_recent_mfa(user)
    return user


async def require_recent_developer_mfa(
    user: Annotated[CurrentUser, Depends(require_recent_support_mfa)],
) -> CurrentUser:
    if not user.is_developer:
        raise PermissionDeniedError("Developer privileges required")
    if user.tenant_id is not None or user.support_access_session_id is not None:
        raise PermissionDeniedError("Global Developer context required")
    return user


def ensure_platform_capability(user: CurrentUser, code: str) -> None:
    """Enforce a global platform capability outside tenant support context."""

    if user.tenant_id is not None or user.support_access_session_id is not None:
        raise PermissionDeniedError("Global platform context required")
    if code not in user.platform_capabilities:
        raise PermissionDeniedError(f"Missing platform capability: {code}")


def require_platform_capability(code: str):  # type: ignore[no-untyped-def]
    """Require a DB-backed platform capability in the global context."""

    async def _checker(
        user: Annotated[CurrentUser, Depends(current_user)],
    ) -> CurrentUser:
        ensure_platform_capability(user, code)
        return user

    # Route-inventory tests inspect this marker so a new admin route cannot
    # accidentally rely only on the privileged database pool.
    _checker.platform_capability_code = code  # type: ignore[attr-defined]
    return _checker


def require_recent_platform_capability(code: str):  # type: ignore[no-untyped-def]
    """Require a platform capability plus a recent MFA step-up."""

    async def _checker(
        user: Annotated[CurrentUser, Depends(require_recent_support_mfa)],
    ) -> CurrentUser:
        ensure_platform_capability(user, code)
        return user

    _checker.platform_capability_code = code  # type: ignore[attr-defined]
    return _checker


async def require_support(
    user: Annotated[CurrentUser, Depends(current_user)],
) -> CurrentUser:
    if not (user.is_developer or user.is_administrator):
        raise PermissionDeniedError("Support privileges required")
    return user


async def require_tenant_owner(
    user: Annotated[CurrentUser, Depends(current_user)],
) -> CurrentUser:
    """Require protected, active ownership rather than a delegable role grant."""

    if user.tenant_id is None or not user.is_tenant_owner:
        raise PermissionDeniedError("Active pharmacy ownership is required")
    if user.is_developer or user.is_administrator or user.support_access_session_id is not None:
        raise PermissionDeniedError("Platform support cannot act as a pharmacy owner")
    return user


async def require_recent_owner_mfa(
    user: Annotated[CurrentUser, Depends(require_tenant_owner)],
) -> CurrentUser:
    _ensure_recent_mfa(user)
    return user


async def require_recent_mfa_if_support(
    user: Annotated[CurrentUser, Depends(current_user)],
) -> CurrentUser:
    if user.is_developer or user.is_administrator:
        return await require_recent_support_mfa(user)
    return user


def require_permission(code: str):  # type: ignore[no-untyped-def]
    """Dependency factory — declares that a route needs `code`.

    Developer retains the temporary phase-one bypass. Administrator access is
    granted only by explicit support dependencies on admin routes.
    """

    async def _checker(
        user: Annotated[CurrentUser, Depends(current_user)],
    ) -> CurrentUser:
        if user.is_developer and user.support_access_session_id is None:
            return user
        if code in user.permissions:
            return user
        raise PermissionDeniedError(f"Missing permission: {code}")

    _checker.permission_codes = (code,)  # type: ignore[attr-defined]
    return _checker


def require_any_permission(*codes: str):  # type: ignore[no-untyped-def]
    """Dependency factory for routes that allow several equivalent permissions."""

    async def _checker(
        user: Annotated[CurrentUser, Depends(current_user)],
    ) -> CurrentUser:
        if user.is_developer and user.support_access_session_id is None:
            return user
        if any(code in user.permissions for code in codes):
            return user
        raise PermissionDeniedError(f"Missing one of permissions: {', '.join(codes)}")

    _checker.permission_codes = tuple(codes)  # type: ignore[attr-defined]
    return _checker


def require_branch_permission(
    code: str,
    *,
    policy: BranchScopePolicy,
) -> Callable[[CurrentUser], Awaitable[CurrentUser]]:
    """Require a capability and declare how the route enforces its branch scope."""

    async def _checker(
        user: Annotated[CurrentUser, Depends(current_user)],
    ) -> CurrentUser:
        if user.is_developer and user.support_access_session_id is None:
            return user
        if code not in user.permissions:
            raise PermissionDeniedError(f"Missing permission: {code}")
        if user.branch_scope_for(code) == set():
            raise PermissionDeniedError(f"Missing branch scope: {code}")
        return user

    _checker.permission_codes = (code,)  # type: ignore[attr-defined]
    _checker.branch_scope_policy = policy  # type: ignore[attr-defined]
    return _checker


def require_any_branch_permission(
    *codes: str,
    policy: BranchScopePolicy,
) -> Callable[[CurrentUser], Awaitable[CurrentUser]]:
    """Require one of several capabilities and declare the branch gate strategy."""

    async def _checker(
        user: Annotated[CurrentUser, Depends(current_user)],
    ) -> CurrentUser:
        if user.is_developer and user.support_access_session_id is None:
            return user
        if not any(code in user.permissions for code in codes):
            raise PermissionDeniedError(f"Missing one of permissions: {', '.join(codes)}")
        if user.branch_scope_for_any(*codes) == set():
            raise PermissionDeniedError("Missing usable branch scope")
        return user

    _checker.permission_codes = tuple(codes)  # type: ignore[attr-defined]
    _checker.branch_scope_policy = policy  # type: ignore[attr-defined]
    return _checker


def require_tenant_permission(code: str):  # type: ignore[no-untyped-def]
    """Require a capability granted by a tenant-wide assignment."""

    async def _checker(
        user: Annotated[CurrentUser, Depends(current_user)],
    ) -> CurrentUser:
        has_developer_bypass = user.is_developer and user.support_access_session_id is None
        if not has_developer_bypass and code not in user.permissions:
            raise PermissionDeniedError(f"Missing permission: {code}")
        if user.has_tenant_scope(code):
            return user
        raise PermissionDeniedError(f"Tenant-wide permission required: {code}")

    _checker.permission_codes = (code,)  # type: ignore[attr-defined]
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
