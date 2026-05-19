"""FastAPI dependencies.

`get_db` picks the app or support pool based on the auth context populated by
`AuthContextMiddleware`, opens a transaction, and seeds the RLS GUCs:
    app.tenant_id      — UUID of the active tenant
    app.user_id        — UUID of the acting user
    app.support_session — 'true' when a support/dev session is bypassing RLS

`current_user` decodes the access token and assembles a CurrentUser snapshot.
Permission loading from the DB + Redis cache will be wired up after the roles
domain (migration 0004) lands; right now CurrentUser.permissions is always an
empty set, and routes that need fine-grained perms must wait until then.
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
    # Permissions are populated by the roles domain (migration 0004). Until
    # then this is an empty set and permission checks below should be guarded
    # by `is_developer` / `is_administrator` (those bypass per the spec).
    permissions: set[str] = field(default_factory=set)
    branch_assignments: dict[str, str] = field(default_factory=dict)


async def current_user(
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

    return CurrentUser(
        user_id=user_id,
        tenant_id=tenant_id,
        is_developer=bool(claims.get("is_developer", False)),
        is_administrator=bool(claims.get("is_administrator", False)),
    )


def require_permission(code: str):  # type: ignore[no-untyped-def]
    """Dependency factory — declares that a route needs `code`.

    Until the roles domain lands, only developers / administrators pass any
    permission check (matches the spec: levels 1-2 see everything). Regular
    users get a 403 from here.
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
