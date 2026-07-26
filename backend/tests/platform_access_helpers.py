"""Test-only factories for protected platform accounts."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.auth.models import AppUser


async def _set_actor(session: AsyncSession, user_id: str) -> None:
    await session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": user_id},
    )


async def _active_developer_ids(session: AsyncSession) -> list[str]:
    return [str(user_id) for user_id in await session.scalars(text("""
                SELECT platform_grant.user_id
                FROM public.platform_access_grant AS platform_grant
                JOIN public.app_user AS account
                  ON account.id = platform_grant.user_id
                 AND account.status = 'active'
                WHERE platform_grant.access_kind = 'developer'
                  AND platform_grant.status = 'active'
                ORDER BY platform_grant.requested_at, platform_grant.user_id
                """))]


async def _insert_user(
    session: AsyncSession,
    *,
    email: str,
    full_name: str,
    password_hash: str | None,
    is_developer: bool,
    status: str,
    activated_at: datetime | None,
) -> AppUser:
    user = AppUser(
        email=email,
        full_name=full_name,
        password_hash=password_hash,
        is_developer=is_developer,
        is_administrator=False,
        status=status,
        activated_at=activated_at,
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


async def _grant_for_test(
    session: AsyncSession,
    *,
    user: AppUser,
    access_kind: str,
    developer_ids: list[str],
) -> None:
    requester_id = developer_ids[0]
    await _set_actor(session, requester_id)
    requires_approval = len(developer_ids) > 1
    grant = (
        (
            await session.execute(
                text("""
                    INSERT INTO public.platform_access_grant (
                      user_id,
                      access_kind,
                      status,
                      requested_by,
                      request_reason_code,
                      request_reason,
                      requires_approval,
                      approval_expires_at
                    ) VALUES (
                      :user_id,
                      :access_kind,
                      :status,
                      CAST(:requester_id AS UUID),
                      'other',
                      'Test fixture platform access setup',
                      :requires_approval,
                      CASE
                        WHEN :requires_approval
                        THEN statement_timestamp() + INTERVAL '15 minutes'
                        ELSE NULL
                      END
                    )
                    RETURNING id, version
                    """),
                {
                    "user_id": user.id,
                    "access_kind": access_kind,
                    "status": "pending" if requires_approval else "active",
                    "requester_id": requester_id,
                    "requires_approval": requires_approval,
                },
            )
        )
        .mappings()
        .one()
    )
    if requires_approval:
        await _set_actor(session, developer_ids[1])
        await session.execute(
            text("""
                UPDATE public.platform_access_grant
                SET
                  status = 'active',
                  approved_by = CAST(:approver_id AS UUID),
                  approved_at = statement_timestamp(),
                  approval_reason_code = 'other',
                  approval_reason = 'Test fixture independent approval',
                  version = version + 1,
                  updated_at = statement_timestamp()
                WHERE id = :grant_id
                  AND version = :version
                """),
            {
                "approver_id": developer_ids[1],
                "grant_id": grant["id"],
                "version": grant["version"],
            },
        )
    await session.refresh(user)


async def create_test_platform_user(
    session: AsyncSession,
    *,
    access_kind: str,
    email: str | None = None,
    full_name: str = "Test Platform User",
    password_hash: str | None = None,
    status: str = "active",
    activated_at: datetime | None = None,
) -> AppUser:
    if access_kind not in {"developer", "administrator"}:
        raise ValueError("Unsupported test platform access kind")
    if status != "active":
        raise ValueError("Platform test accounts must be active")

    previous_actor = str(
        await session.scalar(text("SELECT current_setting('app.user_id', true)")) or ""
    )
    await session.execute(text("SELECT set_config('app.support_session', 'true', true)"))
    try:
        developer_ids = await _active_developer_ids(session)
        if not developer_ids:
            developer_grant_count = int(
                await session.scalar(
                    text(
                        "SELECT count(*) FROM public.platform_access_grant "
                        "WHERE access_kind = 'developer'"
                    )
                )
                or 0
            )
            if developer_grant_count:
                raise RuntimeError("Test database has platform history without an active Developer")
            await _set_actor(session, "")
            bootstrap = await _insert_user(
                session,
                email=(
                    email or f"platform-{uuid4().hex}@example.invalid"
                    if access_kind == "developer"
                    else f"fixture-bootstrap-{uuid4().hex}@example.invalid"
                ),
                full_name=(
                    full_name if access_kind == "developer" else "Fixture Bootstrap Developer"
                ),
                password_hash=password_hash if access_kind == "developer" else None,
                is_developer=True,
                status="active",
                activated_at=activated_at,
            )
            if access_kind == "developer":
                return bootstrap
            developer_ids = [str(bootstrap.id)]

        user = await _insert_user(
            session,
            email=email or f"platform-{uuid4().hex}@example.invalid",
            full_name=full_name,
            password_hash=password_hash,
            is_developer=False,
            status=status,
            activated_at=activated_at,
        )
        await _grant_for_test(
            session,
            user=user,
            access_kind=access_kind,
            developer_ids=developer_ids,
        )
        return user
    finally:
        await _set_actor(session, previous_actor)


async def make_test_developer_sole(
    session: AsyncSession,
    developer: AppUser,
) -> None:
    """Revoke other test Developers inside the caller's rollback-only transaction."""

    previous_actor = str(
        await session.scalar(text("SELECT current_setting('app.user_id', true)")) or ""
    )
    await session.execute(text("SELECT set_config('app.support_session', 'true', true)"))
    try:
        await _set_actor(session, str(developer.id))
        await session.execute(
            text("""
                UPDATE public.platform_access_grant
                SET
                  status = 'revoked',
                  revoked_by = :developer_id,
                  revoked_at = statement_timestamp(),
                  revoke_reason_code = 'other',
                  revoke_reason = 'Test fixture sole Developer isolation',
                  version = version + 1,
                  updated_at = statement_timestamp()
                WHERE access_kind = 'developer'
                  AND status = 'active'
                  AND user_id <> :developer_id
                """),
            {"developer_id": developer.id},
        )
    finally:
        await _set_actor(session, previous_actor)


async def archive_test_platform_user(
    session: AsyncSession,
    *,
    user: AppUser,
) -> None:
    """Soft-delete a committed platform fixture through the protected DB path."""

    previous_actor = str(
        await session.scalar(text("SELECT current_setting('app.user_id', true)")) or ""
    )
    await session.execute(text("SELECT set_config('app.support_session', 'true', true)"))
    try:
        developer_ids = [
            user_id for user_id in await _active_developer_ids(session) if user_id != str(user.id)
        ]
        if not developer_ids:
            replacement = await create_test_platform_user(
                session,
                access_kind="developer",
                full_name="Fixture Cleanup Developer",
            )
            developer_ids = [str(replacement.id)]

        await _set_actor(session, developer_ids[0])
        await session.execute(
            text("""
                UPDATE public.app_user
                SET status = 'archived', updated_at = statement_timestamp()
                WHERE id = :user_id
                  AND status <> 'archived'
                """),
            {"user_id": user.id},
        )
        await session.refresh(user)
    finally:
        await _set_actor(session, previous_actor)
