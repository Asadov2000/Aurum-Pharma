"""Database access for platform team accounts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class PlatformStaffAccountRecord:
    user_id: UUID
    email: str
    full_name: str
    status: str
    version: int
    invited_at: datetime
    invitation_expires_at: datetime | None
    activated_at: datetime | None
    blocked_at: datetime | None
    offboarded_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _record(row: RowMapping) -> PlatformStaffAccountRecord:
    return PlatformStaffAccountRecord(
        user_id=cast(UUID, row["user_id"]),
        email=str(row["email"]),
        full_name=str(row["full_name"]),
        status=str(row["status"]),
        version=int(cast(int, row["version"])),
        invited_at=cast(datetime, row["invited_at"]),
        invitation_expires_at=cast(datetime | None, row["invitation_expires_at"]),
        activated_at=cast(datetime | None, row["activated_at"]),
        blocked_at=cast(datetime | None, row["blocked_at"]),
        offboarded_at=cast(datetime | None, row["offboarded_at"]),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
    )


_ACCOUNT_COLUMNS = """
  profile.user_id,
  account.email,
  account.full_name,
  profile.status,
  profile.version,
  profile.invited_at,
  profile.invitation_expires_at,
  profile.activated_at,
  profile.blocked_at,
  profile.offboarded_at,
  profile.created_at,
  profile.updated_at
"""


class PlatformAccountsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def actor_has_capability(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        capability: str,
    ) -> bool:
        return bool(
            await self.session.scalar(
                text(
                    "SELECT public.platform_actor_has_capability("
                    ":actor_user_id, :actor_session_id, :capability)"
                ),
                {
                    "actor_user_id": actor_user_id,
                    "actor_session_id": actor_session_id,
                    "capability": capability,
                },
            )
        )

    async def list_accounts(
        self,
        *,
        query: str | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[PlatformStaffAccountRecord], int]:
        filters = """
          (CAST(:status AS TEXT) IS NULL OR profile.status = CAST(:status AS TEXT))
          AND (
            CAST(:query AS TEXT) IS NULL
            OR account.email ILIKE '%' || CAST(:query AS TEXT) || '%'
            OR account.full_name ILIKE '%' || CAST(:query AS TEXT) || '%'
          )
        """
        parameters = {
            "query": query,
            "status": status,
            "limit": limit,
            "offset": offset,
        }
        total = int(
            await self.session.scalar(
                text(f"""
                    SELECT count(*)
                    FROM public.platform_staff_account AS profile
                    JOIN public.app_user AS account ON account.id = profile.user_id
                    WHERE {filters}
                    """),
                parameters,
            )
            or 0
        )
        rows = (
            await self.session.execute(
                text(f"""
                    SELECT {_ACCOUNT_COLUMNS}
                    FROM public.platform_staff_account AS profile
                    JOIN public.app_user AS account ON account.id = profile.user_id
                    WHERE {filters}
                    ORDER BY profile.invited_at DESC, profile.user_id
                    LIMIT :limit OFFSET :offset
                    """),
                parameters,
            )
        ).mappings()
        return [_record(row) for row in rows], total

    async def create_invitation(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        email: str,
        full_name: str,
        token_hash: str,
        expires_at: datetime,
    ) -> PlatformStaffAccountRecord:
        row = (
            (
                await self.session.execute(
                    text("""
                        SELECT *
                        FROM public.create_platform_staff_invitation(
                          :actor_user_id,
                          :actor_session_id,
                          :email,
                          :full_name,
                          :token_hash,
                          :expires_at
                        )
                        """),
                    {
                        "actor_user_id": actor_user_id,
                        "actor_session_id": actor_session_id,
                        "email": email,
                        "full_name": full_name,
                        "token_hash": token_hash,
                        "expires_at": expires_at,
                    },
                )
            )
            .mappings()
            .one()
        )
        return _record(row)

    async def invitation_is_usable(self, token_hash: str) -> bool:
        return bool(
            await self.session.scalar(
                text("SELECT public.platform_staff_invitation_is_usable(:token_hash)"),
                {"token_hash": token_hash},
            )
        )

    async def accept_invitation(self, *, token_hash: str, password_hash: str) -> UUID | None:
        return cast(
            UUID | None,
            await self.session.scalar(
                text(
                    "SELECT public.accept_platform_staff_invitation(" ":token_hash, :password_hash)"
                ),
                {"token_hash": token_hash, "password_hash": password_hash},
            ),
        )
