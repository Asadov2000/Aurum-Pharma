"""Database access for the auth domain. No business logic here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import cast
from uuid import UUID

from sqlalchemy import delete, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.auth.models import EmailCode, Session


@dataclass(frozen=True)
class AuthUserRecord:
    id: UUID
    email: str
    full_name: str
    password_hash: str | None
    is_developer: bool
    is_administrator: bool
    home_tenant_id: UUID | None
    status: str
    membership_status: str | None
    last_login_at: datetime | None
    password_required: bool


@dataclass(frozen=True)
class EmailCodeChallenge:
    id: UUID
    code_salt: str


@dataclass(frozen=True)
class AuthSessionRecord:
    id: UUID
    user_id: UUID
    expires_at: datetime
    reuse_presented_token: bool


class EmailCodeIssueStatus(StrEnum):
    CREATED = "created"
    RATE_LIMIT_MINUTE = "rate_limit_minute"
    RATE_LIMIT_HOUR = "rate_limit_hour"


def _auth_user_from_row(row: RowMapping) -> AuthUserRecord:
    return AuthUserRecord(
        id=cast(UUID, row["id"]),
        email=cast(str, row["email"]),
        full_name=cast(str, row["full_name"]),
        password_hash=cast(str | None, row["password_hash"]),
        is_developer=bool(row["is_developer"]),
        is_administrator=bool(row["is_administrator"]),
        home_tenant_id=cast(UUID | None, row["home_tenant_id"]),
        status=cast(str, row["status"]),
        membership_status=cast(str | None, row["membership_status"]),
        last_login_at=cast(datetime | None, row["last_login_at"]),
        password_required=bool(row["password_required"]),
    )


def _auth_session_from_row(row: RowMapping) -> AuthSessionRecord:
    return AuthSessionRecord(
        id=cast(UUID, row["id"]),
        user_id=cast(UUID, row["user_id"]),
        expires_at=cast(datetime, row["expires_at"]),
        reuse_presented_token=bool(row["reuse_presented_token"]),
    )


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -------------------------------------------------------------------------
    # app_user
    # -------------------------------------------------------------------------

    async def get_login_user_by_email(
        self,
        *,
        email: str,
        code_id: UUID,
        candidate_hash: str,
    ) -> AuthUserRecord | None:
        result = await self.session.execute(
            text(
                "SELECT * FROM public.lookup_login_user_by_email("
                ":email, :code_id, :candidate_hash)"
            ),
            {
                "email": email,
                "code_id": code_id,
                "candidate_hash": candidate_hash,
            },
        )
        row = result.mappings().one_or_none()
        return _auth_user_from_row(row) if row is not None else None

    async def get_user_by_id(
        self, user_id: UUID, *, session_id: UUID | None = None
    ) -> AuthUserRecord | None:
        result = await self.session.execute(
            text("SELECT * FROM public.lookup_auth_user_by_id(:user_id, :session_id)"),
            {"user_id": user_id, "session_id": session_id},
        )
        row = result.mappings().one_or_none()
        return _auth_user_from_row(row) if row is not None else None

    async def touch_last_login(self, user_id: UUID, session_id: UUID) -> None:
        await self.session.execute(
            text("SELECT public.touch_auth_user_last_login(:user_id, :session_id)"),
            {"user_id": user_id, "session_id": session_id},
        )

    # -------------------------------------------------------------------------
    # email_code
    # -------------------------------------------------------------------------

    async def issue_login_email_code(
        self,
        *,
        email_lower: str,
        code_hash: str,
        code_salt: str,
        ip_address: str,
        user_agent: str | None,
    ) -> EmailCodeIssueStatus:
        result = await self.session.execute(
            text(
                "SELECT public.issue_auth_email_code("
                ":email, :code_hash, :code_salt, :ip_address, :user_agent)"
            ),
            {
                "email": email_lower,
                "code_hash": code_hash,
                "code_salt": code_salt,
                "ip_address": ip_address,
                "user_agent": user_agent,
            },
        )
        return EmailCodeIssueStatus(result.scalar_one())

    async def find_active_email_code(self, email_lower: str) -> EmailCodeChallenge | None:
        result = await self.session.execute(
            text("SELECT * FROM public.find_auth_email_code_challenge(:email)"),
            {"email": email_lower},
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return EmailCodeChallenge(
            id=cast(UUID, row["id"]),
            code_salt=cast(str, row["code_salt"]),
        )

    async def email_code_matches(
        self,
        *,
        code_id: UUID,
        email_lower: str,
        candidate_hash: str,
    ) -> bool:
        result = await self.session.execute(
            text("SELECT public.auth_email_code_matches(" ":code_id, :email, :candidate_hash)"),
            {
                "code_id": code_id,
                "email": email_lower,
                "candidate_hash": candidate_hash,
            },
        )
        return bool(result.scalar_one())

    async def consume_email_code(
        self,
        *,
        code_id: UUID,
        email_lower: str,
        candidate_hash: str,
    ) -> bool:
        result = await self.session.execute(
            text("SELECT public.consume_auth_email_code(" ":code_id, :email, :candidate_hash)"),
            {
                "code_id": code_id,
                "email": email_lower,
                "candidate_hash": candidate_hash,
            },
        )
        return bool(result.scalar_one())

    async def delete_expired_email_codes(self, older_than: datetime) -> int:
        result = await self.session.execute(
            delete(EmailCode).where(EmailCode.expires_at < older_than)
        )
        return result.rowcount or 0  # type: ignore[attr-defined]

    # -------------------------------------------------------------------------
    # login_attempt
    # -------------------------------------------------------------------------

    async def insert_login_attempt(
        self,
        *,
        email_lower: str,
        user_id: UUID | None,
        ip_address: str,
        user_agent: str | None,
        outcome: str,
        reason: str | None = None,
    ) -> None:
        await self.session.execute(
            text(
                "SELECT public.record_auth_login_attempt("
                ":email, :user_id, :ip_address, :user_agent, :outcome, :reason)"
            ),
            {
                "email": email_lower,
                "user_id": user_id,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "outcome": outcome,
                "reason": reason,
            },
        )

    async def enforce_login_guard(self, *, email_lower: str, ip_address: str) -> bool:
        result = await self.session.execute(
            text("SELECT public.enforce_auth_login_guard(:email, :ip_address)"),
            {"email": email_lower, "ip_address": ip_address},
        )
        return bool(result.scalar_one())

    # -------------------------------------------------------------------------
    # session
    # -------------------------------------------------------------------------

    async def create_session_from_email_code(
        self,
        *,
        code_id: UUID,
        email_lower: str,
        candidate_hash: str,
        refresh_token_hash: str,
        user_agent: str | None,
        ip_address: str | None,
        expires_at: datetime,
    ) -> UUID | None:
        result = await self.session.execute(
            text(
                "SELECT public.create_auth_session_from_email_code("
                ":code_id, :email, :candidate_hash, :refresh_token_hash, "
                ":user_agent, :ip_address, :expires_at)"
            ),
            {
                "code_id": code_id,
                "email": email_lower,
                "candidate_hash": candidate_hash,
                "refresh_token_hash": refresh_token_hash,
                "user_agent": user_agent,
                "ip_address": ip_address,
                "expires_at": expires_at,
            },
        )
        return cast(UUID | None, result.scalar_one())

    async def accept_tenant_invitation(
        self,
        *,
        session_id: UUID,
        tenant_id: UUID | None,
        accepted_at: datetime,
    ) -> int:
        result = await self.session.execute(
            text(
                "SELECT public.accept_tenant_invitation(" ":session_id, :tenant_id, :accepted_at)"
            ),
            {
                "session_id": session_id,
                "tenant_id": tenant_id,
                "accepted_at": accepted_at,
            },
        )
        return int(result.scalar_one())

    async def rotate_session(
        self,
        *,
        old_token_hash: str,
        new_token_hash: str,
        operation_id: UUID,
        user_agent: str | None,
        ip_address: str | None,
        expires_at: datetime,
    ) -> AuthSessionRecord | None:
        result = await self.session.execute(
            text(
                "SELECT * FROM public.rotate_auth_session("
                ":old_token_hash, :new_token_hash, :operation_id, "
                ":user_agent, :ip_address, :expires_at)"
            ),
            {
                "old_token_hash": old_token_hash,
                "new_token_hash": new_token_hash,
                "operation_id": operation_id,
                "user_agent": user_agent,
                "ip_address": ip_address,
                "expires_at": expires_at,
            },
        )
        row = result.mappings().one_or_none()
        return _auth_session_from_row(row) if row is not None else None

    async def revoke_session_by_hash(
        self,
        token_hash: str,
        *,
        reason: str,
        operation_id: UUID | None = None,
    ) -> UUID | None:
        result = await self.session.execute(
            text(
                "SELECT public.revoke_auth_session_by_hash(" ":token_hash, :reason, :operation_id)"
            ),
            {
                "token_hash": token_hash,
                "reason": reason,
                "operation_id": operation_id,
            },
        )
        return cast(UUID | None, result.scalar_one())

    async def delete_expired_sessions(self, older_than: datetime) -> int:
        result = await self.session.execute(delete(Session).where(Session.expires_at < older_than))
        return result.rowcount or 0  # type: ignore[attr-defined]
