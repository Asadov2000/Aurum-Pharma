"""Database access for short-lived tenant support sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class SupportCapabilityRecord:
    code: str
    group_code: str
    name: str
    description: str | None
    is_dangerous: bool
    risk_level: str


@dataclass(frozen=True)
class SupportAccessSessionRecord:
    id: UUID
    tenant_id: UUID
    tenant_name: str
    actor_user_id: UUID
    reason: str
    capabilities: tuple[str, ...]
    is_read_only: bool
    started_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


def _session_record(row: object) -> SupportAccessSessionRecord:
    mapping = cast(dict[str, object], row)
    return SupportAccessSessionRecord(
        id=cast(UUID, mapping["id"]),
        tenant_id=cast(UUID, mapping["tenant_id"]),
        tenant_name=cast(str, mapping["tenant_name"]),
        actor_user_id=cast(UUID, mapping["actor_user_id"]),
        reason=cast(str, mapping["reason"]),
        capabilities=tuple(cast(list[str], mapping["capabilities"])),
        is_read_only=bool(mapping["is_read_only"]),
        started_at=cast(datetime, mapping["started_at"]),
        expires_at=cast(datetime, mapping["expires_at"]),
        revoked_at=cast(datetime | None, mapping["revoked_at"]),
    )


_SESSION_SELECT = """
SELECT
  access_session.id,
  access_session.tenant_id,
  tenant.name AS tenant_name,
  access_session.actor_user_id,
  access_session.reason,
  access_session.is_read_only,
  access_session.started_at,
  access_session.expires_at,
  access_session.revoked_at,
  COALESCE(
    array_agg(capability.permission_code ORDER BY capability.permission_code)
      FILTER (WHERE capability.permission_code IS NOT NULL),
    ARRAY[]::TEXT[]
  ) AS capabilities
FROM public.support_access_session AS access_session
JOIN public.tenant AS tenant ON tenant.id = access_session.tenant_id
LEFT JOIN public.support_access_capability AS capability
  ON capability.support_access_session_id = access_session.id
 AND capability.tenant_id = access_session.tenant_id
"""

_SESSION_GROUP_BY = """
GROUP BY
  access_session.id,
  tenant.name
"""

_AUTH_LINEAGE_CTE = """
WITH RECURSIVE auth_lineage AS (
  SELECT auth_session.id, auth_session.rotated_from_session_id
  FROM public.session AS auth_session
  WHERE auth_session.id = :actor_session_id
    AND auth_session.user_id = :actor_user_id
    AND auth_session.revoked_at IS NULL
    AND auth_session.expires_at > statement_timestamp()

  UNION ALL

  SELECT parent.id, parent.rotated_from_session_id
  FROM public.session AS parent
  JOIN auth_lineage AS child ON parent.id = child.rotated_from_session_id
  WHERE parent.user_id = :actor_user_id
)
"""


class SupportAccessRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_actor(self, actor_user_id: UUID) -> tuple[bool, bool, str] | None:
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT is_developer, is_administrator, status "
                        "FROM public.app_user WHERE id = :actor_user_id FOR UPDATE"
                    ),
                    {"actor_user_id": actor_user_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return bool(row["is_developer"]), bool(row["is_administrator"]), str(row["status"])

    async def tenant_status(self, tenant_id: UUID) -> str | None:
        return cast(
            str | None,
            (
                await self.session.execute(
                    text("SELECT status FROM public.tenant " "WHERE id = :tenant_id FOR SHARE"),
                    {"tenant_id": tenant_id},
                )
            ).scalar_one_or_none(),
        )

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

    async def list_capabilities(
        self,
        *,
        is_developer: bool,
        is_administrator: bool,
        allowed_codes: tuple[str, ...],
    ) -> list[SupportCapabilityRecord]:
        grant_column = "developer_grantable" if is_developer else "administrator_grantable"
        if not is_developer and not is_administrator:
            return []
        statement = text(f"""
            SELECT code, group_code, name, description, is_dangerous, risk_level
            FROM public.permission
            WHERE is_active
              AND target_role_type = 'tenant'
              AND {grant_column}
              AND code = ANY(CAST(:allowed_codes AS TEXT[]))
            ORDER BY group_code, code
            """)
        rows = (
            await self.session.execute(
                statement,
                {"allowed_codes": list(allowed_codes)},
            )
        ).mappings()
        return [
            SupportCapabilityRecord(
                code=str(row["code"]),
                group_code=str(row["group_code"]),
                name=str(row["name"]),
                description=cast(str | None, row["description"]),
                is_dangerous=bool(row["is_dangerous"]),
                risk_level=str(row["risk_level"]),
            )
            for row in rows
        ]

    async def revoke_active_for_actor(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        revoked_at: datetime,
    ) -> list[SupportAccessSessionRecord]:
        ids = list(
            (
                await self.session.execute(
                    text(_AUTH_LINEAGE_CTE + """
                        UPDATE public.support_access_session
                        SET
                          revoked_at = :revoked_at,
                          revoked_by_user_id = :actor_user_id,
                          updated_at = :revoked_at,
                          updated_by = :actor_user_id
                        WHERE actor_user_id = :actor_user_id
                          AND actor_session_id IN (
                            SELECT auth_lineage.id FROM auth_lineage
                          )
                          AND revoked_at IS NULL
                        RETURNING id
                        """),
                    {
                        "actor_user_id": actor_user_id,
                        "actor_session_id": actor_session_id,
                        "revoked_at": revoked_at,
                    },
                )
            ).scalars()
        )
        records: list[SupportAccessSessionRecord] = []
        for session_id in ids:
            record = await self.get_for_actor(
                session_id=cast(UUID, session_id),
                actor_user_id=actor_user_id,
            )
            if record is not None:
                records.append(record)
        return records

    async def create_session(
        self,
        *,
        tenant_id: UUID,
        actor_user_id: UUID,
        actor_session_id: UUID,
        reason: str,
        capabilities: list[str],
        is_read_only: bool,
        started_at: datetime,
        expires_at: datetime,
    ) -> SupportAccessSessionRecord:
        session_id = cast(
            UUID,
            (
                await self.session.execute(
                    text("""
                        INSERT INTO public.support_access_session (
                          tenant_id,
                          actor_user_id,
                          actor_session_id,
                          reason,
                          is_read_only,
                          started_at,
                          expires_at,
                          created_by,
                          updated_by
                        ) VALUES (
                          :tenant_id,
                          :actor_user_id,
                          :actor_session_id,
                          :reason,
                          :is_read_only,
                          :started_at,
                          :expires_at,
                          :actor_user_id,
                          :actor_user_id
                        )
                        RETURNING id
                        """),
                    {
                        "tenant_id": tenant_id,
                        "actor_user_id": actor_user_id,
                        "actor_session_id": actor_session_id,
                        "reason": reason,
                        "is_read_only": is_read_only,
                        "started_at": started_at,
                        "expires_at": expires_at,
                    },
                )
            ).scalar_one(),
        )
        await self.session.execute(
            text("""
                INSERT INTO public.support_access_capability (
                  support_access_session_id,
                  tenant_id,
                  permission_code,
                  created_by
                )
                SELECT :session_id, :tenant_id, code, :actor_user_id
                FROM unnest(CAST(:capabilities AS TEXT[])) AS code
                """),
            {
                "session_id": session_id,
                "tenant_id": tenant_id,
                "actor_user_id": actor_user_id,
                "capabilities": capabilities,
            },
        )
        record = await self.get_for_actor(
            session_id=session_id,
            actor_user_id=actor_user_id,
        )
        if record is None:
            raise RuntimeError("Created support session is unavailable")
        return record

    async def get_for_actor(
        self,
        *,
        session_id: UUID,
        actor_user_id: UUID,
    ) -> SupportAccessSessionRecord | None:
        row = (
            (
                await self.session.execute(
                    text(
                        _SESSION_SELECT
                        + " WHERE access_session.id = :session_id "
                        + "AND access_session.actor_user_id = :actor_user_id "
                        + _SESSION_GROUP_BY
                    ),
                    {"session_id": session_id, "actor_user_id": actor_user_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _session_record(dict(row))

    async def list_active_for_actor(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        now: datetime,
    ) -> list[SupportAccessSessionRecord]:
        rows = (
            await self.session.execute(
                text(
                    _AUTH_LINEAGE_CTE
                    + _SESSION_SELECT
                    + " WHERE access_session.actor_user_id = :actor_user_id "
                    + "AND access_session.actor_session_id IN ("
                    + "SELECT auth_lineage.id FROM auth_lineage) "
                    + "AND access_session.revoked_at IS NULL "
                    + "AND access_session.expires_at > :now "
                    + _SESSION_GROUP_BY
                    + " ORDER BY access_session.started_at DESC"
                ),
                {
                    "actor_user_id": actor_user_id,
                    "actor_session_id": actor_session_id,
                    "now": now,
                },
            )
        ).mappings()
        return [_session_record(dict(row)) for row in rows]

    async def revoke_session(
        self,
        *,
        session_id: UUID,
        actor_user_id: UUID,
        actor_session_id: UUID,
        revoked_at: datetime,
    ) -> SupportAccessSessionRecord | None:
        updated = (
            await self.session.execute(
                text(_AUTH_LINEAGE_CTE + """
                    UPDATE public.support_access_session
                    SET
                      revoked_at = :revoked_at,
                      revoked_by_user_id = :actor_user_id,
                      updated_at = :revoked_at,
                      updated_by = :actor_user_id
                    WHERE id = :session_id
                      AND actor_user_id = :actor_user_id
                      AND actor_session_id IN (
                        SELECT auth_lineage.id FROM auth_lineage
                      )
                      AND revoked_at IS NULL
                    RETURNING id
                    """),
                {
                    "session_id": session_id,
                    "actor_user_id": actor_user_id,
                    "actor_session_id": actor_session_id,
                    "revoked_at": revoked_at,
                },
            )
        ).scalar_one_or_none()
        if updated is None:
            return None
        return await self.get_for_actor(
            session_id=session_id,
            actor_user_id=actor_user_id,
        )
