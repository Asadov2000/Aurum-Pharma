"""Database access for protected platform access grants."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class PlatformActorRecord:
    status: str
    is_developer: bool
    has_active_developer_grant: bool
    capabilities: frozenset[str]


@dataclass(frozen=True)
class PlatformTargetRecord:
    status: str
    home_tenant_id: UUID | None
    has_membership: bool
    is_developer: bool
    is_administrator: bool


@dataclass(frozen=True)
class PlatformAccessGrantRecord:
    id: UUID
    user_id: UUID
    access_kind: str
    status: str
    requested_by: UUID | None
    request_reason_code: str
    request_reason: str
    requested_at: datetime
    requires_approval: bool
    approval_expires_at: datetime | None
    approved_by: UUID | None
    approved_at: datetime | None
    approval_reason_code: str | None
    approval_reason: str | None
    revoked_by: UUID | None
    revoked_at: datetime | None
    revoke_reason_code: str | None
    revoke_reason: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class PlatformCapabilityRecord:
    code: str
    group_code: str
    name: str
    description: str | None
    risk_level: str
    requires_step_up: bool
    requires_confirmation: bool


def _grant_record(row: RowMapping) -> PlatformAccessGrantRecord:
    return PlatformAccessGrantRecord(
        id=cast(UUID, row["id"]),
        user_id=cast(UUID, row["user_id"]),
        access_kind=str(row["access_kind"]),
        status=str(row["status"]),
        requested_by=cast(UUID | None, row["requested_by"]),
        request_reason_code=str(row["request_reason_code"]),
        request_reason=str(row["request_reason"]),
        requested_at=cast(datetime, row["requested_at"]),
        requires_approval=bool(row["requires_approval"]),
        approval_expires_at=cast(datetime | None, row["approval_expires_at"]),
        approved_by=cast(UUID | None, row["approved_by"]),
        approved_at=cast(datetime | None, row["approved_at"]),
        approval_reason_code=cast(str | None, row["approval_reason_code"]),
        approval_reason=cast(str | None, row["approval_reason"]),
        revoked_by=cast(UUID | None, row["revoked_by"]),
        revoked_at=cast(datetime | None, row["revoked_at"]),
        revoke_reason_code=cast(str | None, row["revoke_reason_code"]),
        revoke_reason=cast(str | None, row["revoke_reason"]),
        version=int(cast(int, row["version"])),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
        capabilities=(),
    )


_GRANT_COLUMNS = """
  id,
  user_id,
  access_kind,
  status,
  requested_by,
  request_reason_code,
  request_reason,
  requested_at,
  requires_approval,
  approval_expires_at,
  approved_by,
  approved_at,
  approval_reason_code,
  approval_reason,
  revoked_by,
  revoked_at,
  revoke_reason_code,
  revoke_reason,
  version,
  created_at,
  updated_at
"""


class PlatformAccessRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def acquire_control_plane_lock(self) -> None:
        await self.session.execute(text("SELECT pg_advisory_xact_lock(7148, 1)"))

    async def lock_actor(self, actor_user_id: UUID) -> PlatformActorRecord | None:
        row = (
            (
                await self.session.execute(
                    text("""
                        SELECT
                          account.status,
                          account.is_developer,
                          EXISTS (
                            SELECT 1
                            FROM public.platform_access_grant AS active_grant
                            WHERE active_grant.user_id = account.id
                              AND active_grant.access_kind = 'developer'
                              AND active_grant.status = 'active'
                          ) AS has_active_developer_grant
                          , ARRAY(
                            SELECT assignment.permission_code
                            FROM public.platform_access_grant AS actor_capability_grant
                            JOIN public.platform_access_grant_permission AS assignment
                              ON assignment.grant_id = actor_capability_grant.id
                            JOIN public.permission AS permission
                              ON permission.code = assignment.permission_code
                             AND permission.is_active
                             AND permission.target_role_type = 'platform'
                             AND permission.scope_type = 'PLATFORM'
                            WHERE actor_capability_grant.user_id = account.id
                              AND actor_capability_grant.status = 'active'
                            ORDER BY assignment.permission_code
                          ) AS capabilities
                        FROM public.app_user AS account
                        WHERE account.id = :actor_user_id
                        FOR UPDATE
                        """),
                    {"actor_user_id": actor_user_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return PlatformActorRecord(
            status=str(row["status"]),
            is_developer=bool(row["is_developer"]),
            has_active_developer_grant=bool(row["has_active_developer_grant"]),
            capabilities=frozenset(str(code) for code in row["capabilities"]),
        )

    async def _capabilities_by_grant(self, grant_ids: list[UUID]) -> dict[UUID, tuple[str, ...]]:
        if not grant_ids:
            return {}
        rows = (
            await self.session.execute(
                text("""
                    SELECT assignment.grant_id, assignment.permission_code
                    FROM public.platform_access_grant_permission AS assignment
                    WHERE assignment.grant_id = ANY(CAST(:grant_ids AS UUID[]))
                    ORDER BY assignment.grant_id, assignment.permission_code
                    """),
                {"grant_ids": grant_ids},
            )
        ).mappings()
        grouped: dict[UUID, list[str]] = {}
        for row in rows:
            grant_id = cast(UUID, row["grant_id"])
            grouped.setdefault(grant_id, []).append(str(row["permission_code"]))
        return {grant_id: tuple(codes) for grant_id, codes in grouped.items()}

    async def _attach_capabilities(
        self, grants: list[PlatformAccessGrantRecord]
    ) -> list[PlatformAccessGrantRecord]:
        capabilities = await self._capabilities_by_grant([grant.id for grant in grants])
        return [replace(grant, capabilities=capabilities.get(grant.id, ())) for grant in grants]

    async def list_grantable_capabilities(
        self,
        *,
        actor_user_id: UUID,
        access_kind: str,
    ) -> list[PlatformCapabilityRecord]:
        grantable_column = (
            "permission.developer_grantable"
            if access_kind == "developer"
            else "permission.administrator_grantable"
        )
        rows = (
            await self.session.execute(
                text(f"""
                    SELECT
                      permission.code,
                      permission.group_code,
                      permission.name,
                      permission.description,
                      permission.risk_level,
                      permission.requires_step_up,
                      permission.requires_confirmation
                    FROM public.platform_access_grant AS actor_grant
                    JOIN public.platform_access_grant_permission AS actor_capability
                      ON actor_capability.grant_id = actor_grant.id
                    JOIN public.permission AS permission
                      ON permission.code = actor_capability.permission_code
                    WHERE actor_grant.user_id = :actor_user_id
                      AND actor_grant.access_kind = 'developer'
                      AND actor_grant.status = 'active'
                      AND permission.is_active
                      AND permission.scope_type = 'PLATFORM'
                      AND permission.target_role_type = 'platform'
                      AND permission.developer_delegable
                      AND {grantable_column}
                    ORDER BY permission.group_code, permission.code
                    """),
                {"actor_user_id": actor_user_id},
            )
        ).mappings()
        return [
            PlatformCapabilityRecord(
                code=str(row["code"]),
                group_code=str(row["group_code"]),
                name=str(row["name"]),
                description=cast(str | None, row["description"]),
                risk_level=str(row["risk_level"]),
                requires_step_up=bool(row["requires_step_up"]),
                requires_confirmation=bool(row["requires_confirmation"]),
            )
            for row in rows
        ]

    async def auth_session_is_active(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
    ) -> bool:
        return bool(
            await self.session.scalar(
                text("""
                    SELECT EXISTS (
                      SELECT 1
                      FROM public.session AS auth_session
                      WHERE auth_session.id = :actor_session_id
                        AND auth_session.user_id = :actor_user_id
                        AND auth_session.revoked_at IS NULL
                        AND auth_session.expires_at > statement_timestamp()
                    )
                    """),
                {
                    "actor_user_id": actor_user_id,
                    "actor_session_id": actor_session_id,
                },
            )
        )

    async def lock_target(self, user_id: UUID) -> PlatformTargetRecord | None:
        row = (
            (
                await self.session.execute(
                    text("""
                        SELECT
                          account.status,
                          account.home_tenant_id,
                          account.is_developer,
                          account.is_administrator,
                          EXISTS (
                            SELECT 1
                            FROM public.tenant_membership AS membership
                            WHERE membership.user_id = account.id
                              AND membership.status IN (
                                'pending',
                                'active',
                                'suspended'
                              )
                          ) AS has_membership
                        FROM public.app_user AS account
                        WHERE account.id = :user_id
                        FOR UPDATE
                        """),
                    {"user_id": user_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return PlatformTargetRecord(
            status=str(row["status"]),
            home_tenant_id=cast(UUID | None, row["home_tenant_id"]),
            has_membership=bool(row["has_membership"]),
            is_developer=bool(row["is_developer"]),
            is_administrator=bool(row["is_administrator"]),
        )

    async def expire_pending_for_target(
        self,
        *,
        user_id: UUID,
        actor_user_id: UUID,
    ) -> list[PlatformAccessGrantRecord]:
        rows = (
            await self.session.execute(
                text(f"""
                    UPDATE public.platform_access_grant
                    SET
                      status = 'expired',
                      revoked_by = :actor_user_id,
                      revoked_at = statement_timestamp(),
                      revoke_reason_code = 'approval_window_expired',
                      revoke_reason = 'Approval window expired',
                      version = version + 1,
                      updated_at = statement_timestamp()
                    WHERE user_id = :user_id
                      AND status = 'pending'
                      AND approval_expires_at <= statement_timestamp()
                    RETURNING {_GRANT_COLUMNS}
                    """),
                {"user_id": user_id, "actor_user_id": actor_user_id},
            )
        ).mappings()
        return await self._attach_capabilities([_grant_record(row) for row in rows])

    async def has_current_grant(self, user_id: UUID) -> bool:
        return bool(
            await self.session.scalar(
                text("""
                    SELECT EXISTS (
                      SELECT 1
                      FROM public.platform_access_grant
                      WHERE user_id = :user_id
                        AND status IN ('pending', 'active')
                    )
                    """),
                {"user_id": user_id},
            )
        )

    async def has_other_active_developer(self, actor_user_id: UUID) -> bool:
        return bool(
            await self.session.scalar(
                text("""
                    SELECT EXISTS (
                      SELECT 1
                      FROM public.platform_access_grant AS active_grant
                      JOIN public.app_user AS account
                        ON account.id = active_grant.user_id
                       AND account.status = 'active'
                      WHERE active_grant.access_kind = 'developer'
                        AND active_grant.status = 'active'
                        AND active_grant.user_id <> :actor_user_id
                    )
                    """),
                {"actor_user_id": actor_user_id},
            )
        )

    async def create_grant(
        self,
        *,
        user_id: UUID,
        access_kind: str,
        actor_user_id: UUID,
        reason_code: str,
        reason: str,
        requires_approval: bool,
        approval_expires_at: datetime | None,
        capabilities: tuple[str, ...],
    ) -> PlatformAccessGrantRecord:
        row = (
            (
                await self.session.execute(
                    text(f"""
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
                          :actor_user_id,
                          :reason_code,
                          :reason,
                          :requires_approval,
                          :approval_expires_at
                        )
                        RETURNING {_GRANT_COLUMNS}
                        """),
                    {
                        "user_id": user_id,
                        "access_kind": access_kind,
                        "status": "pending" if requires_approval else "active",
                        "actor_user_id": actor_user_id,
                        "reason_code": reason_code,
                        "reason": reason,
                        "requires_approval": requires_approval,
                        "approval_expires_at": approval_expires_at,
                    },
                )
            )
            .mappings()
            .one()
        )
        grant = _grant_record(row)
        await self.session.execute(
            text("""
                INSERT INTO public.platform_access_grant_permission (
                  grant_id,
                  permission_code,
                  created_by
                )
                SELECT :grant_id, capability.code, :actor_user_id
                FROM unnest(CAST(:capabilities AS TEXT[])) AS capability(code)
                ORDER BY capability.code
                """),
            {
                "grant_id": grant.id,
                "actor_user_id": actor_user_id,
                "capabilities": list(capabilities),
            },
        )
        return replace(grant, capabilities=capabilities)

    async def lock_grant(self, grant_id: UUID) -> PlatformAccessGrantRecord | None:
        row = (
            (
                await self.session.execute(
                    text(f"""
                        SELECT {_GRANT_COLUMNS}
                        FROM public.platform_access_grant
                        WHERE id = :grant_id
                        FOR UPDATE
                        """),
                    {"grant_id": grant_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return (await self._attach_capabilities([_grant_record(row)]))[0]

    async def approve_grant(
        self,
        *,
        grant_id: UUID,
        actor_user_id: UUID,
        version: int,
        reason_code: str,
        reason: str,
    ) -> PlatformAccessGrantRecord | None:
        row = (
            (
                await self.session.execute(
                    text(f"""
                        UPDATE public.platform_access_grant
                        SET
                          status = 'active',
                          approved_by = :actor_user_id,
                          approved_at = statement_timestamp(),
                          approval_reason_code = :reason_code,
                          approval_reason = :reason,
                          version = version + 1,
                          updated_at = statement_timestamp()
                        WHERE id = :grant_id
                          AND status = 'pending'
                          AND version = :version
                          AND approval_expires_at > statement_timestamp()
                        RETURNING {_GRANT_COLUMNS}
                        """),
                    {
                        "grant_id": grant_id,
                        "actor_user_id": actor_user_id,
                        "version": version,
                        "reason_code": reason_code,
                        "reason": reason,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return (await self._attach_capabilities([_grant_record(row)]))[0]

    async def expire_grant(
        self,
        *,
        grant_id: UUID,
        actor_user_id: UUID,
        version: int,
    ) -> PlatformAccessGrantRecord | None:
        row = (
            (
                await self.session.execute(
                    text(f"""
                        UPDATE public.platform_access_grant
                        SET
                          status = 'expired',
                          revoked_by = :actor_user_id,
                          revoked_at = statement_timestamp(),
                          revoke_reason_code = 'approval_window_expired',
                          revoke_reason = 'Approval window expired',
                          version = version + 1,
                          updated_at = statement_timestamp()
                        WHERE id = :grant_id
                          AND status = 'pending'
                          AND version = :version
                          AND approval_expires_at <= statement_timestamp()
                        RETURNING {_GRANT_COLUMNS}
                        """),
                    {
                        "grant_id": grant_id,
                        "actor_user_id": actor_user_id,
                        "version": version,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return (await self._attach_capabilities([_grant_record(row)]))[0]

    async def revoke_grant(
        self,
        *,
        grant_id: UUID,
        actor_user_id: UUID,
        version: int,
        reason_code: str,
        reason: str,
    ) -> PlatformAccessGrantRecord | None:
        row = (
            (
                await self.session.execute(
                    text(f"""
                        UPDATE public.platform_access_grant
                        SET
                          status = 'revoked',
                          revoked_by = :actor_user_id,
                          revoked_at = statement_timestamp(),
                          revoke_reason_code = :reason_code,
                          revoke_reason = :reason,
                          version = version + 1,
                          updated_at = statement_timestamp()
                        WHERE id = :grant_id
                          AND status IN ('pending', 'active')
                          AND version = :version
                        RETURNING {_GRANT_COLUMNS}
                        """),
                    {
                        "grant_id": grant_id,
                        "actor_user_id": actor_user_id,
                        "version": version,
                        "reason_code": reason_code,
                        "reason": reason,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return (await self._attach_capabilities([_grant_record(row)]))[0]

    async def list_grants(
        self,
        *,
        status: str | None,
        user_id: UUID | None,
        limit: int,
    ) -> list[PlatformAccessGrantRecord]:
        rows = (
            await self.session.execute(
                text(f"""
                    SELECT {_GRANT_COLUMNS}
                    FROM public.platform_access_grant
                    WHERE (
                      CAST(:status AS TEXT) IS NULL
                      OR status = CAST(:status AS TEXT)
                    )
                      AND (
                        CAST(:user_id AS UUID) IS NULL
                        OR user_id = CAST(:user_id AS UUID)
                      )
                    ORDER BY requested_at DESC, id DESC
                    LIMIT :limit
                    """),
                {"status": status, "user_id": user_id, "limit": limit},
            )
        ).mappings()
        return await self._attach_capabilities([_grant_record(row) for row in rows])
