"""Database boundary available only to the isolated authentication mailer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class LoginEmailClaim:
    outbox_id: UUID
    claim_token: UUID
    recipient_email: str
    login_code: str
    code_expires_at: datetime
    attempt_count: int


class AuthMailerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claim_login_email(
        self,
        *,
        encryption_keyring: str,
        lease_seconds: int,
    ) -> LoginEmailClaim | None:
        row = (
            (
                await self.session.execute(
                    text("""
                        SELECT *
                        FROM public.claim_auth_login_email(
                          CAST(:encryption_keyring AS JSONB), :lease_seconds
                        )
                        """),
                    {
                        "encryption_keyring": encryption_keyring,
                        "lease_seconds": lease_seconds,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return LoginEmailClaim(
            outbox_id=cast(UUID, row["outbox_id"]),
            claim_token=cast(UUID, row["claim_token"]),
            recipient_email=str(row["recipient_email"]),
            login_code=str(row["login_code"]),
            code_expires_at=cast(datetime, row["code_expires_at"]),
            attempt_count=int(cast(int, row["attempt_count"])),
        )

    async def complete_login_email(
        self,
        *,
        outbox_id: UUID,
        claim_token: UUID,
        outcome: str,
        error_code: str | None,
    ) -> str | None:
        return cast(
            str | None,
            await self.session.scalar(
                text("""
                    SELECT public.complete_auth_login_email(
                      :outbox_id, :claim_token, :outcome, :error_code
                    )
                    """),
                {
                    "outbox_id": outbox_id,
                    "claim_token": claim_token,
                    "outcome": outcome,
                    "error_code": error_code,
                },
            ),
        )
