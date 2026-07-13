"""Database access for the auth domain. No business logic here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, func, select, text, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.domains.auth.models import EmailCode, LoginAttempt, Session


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
    last_login_at: datetime | None
    password_required: bool


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
        last_login_at=cast(datetime | None, row["last_login_at"]),
        password_required=bool(row["password_required"]),
    )


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -------------------------------------------------------------------------
    # app_user
    # -------------------------------------------------------------------------

    async def get_user_by_email(self, email: str) -> AuthUserRecord | None:
        result = await self.session.execute(
            text("SELECT * FROM public.lookup_auth_user_by_email(:email)"),
            {"email": email},
        )
        row = result.mappings().one_or_none()
        return _auth_user_from_row(row) if row is not None else None

    async def get_user_by_id(
        self, user_id: UUID, *, session_id: UUID | None = None
    ) -> AuthUserRecord | None:
        result = await self.session.execute(
            text("SELECT * FROM public.lookup_auth_user_by_id(" ":user_id, :session_id)"),
            {"user_id": user_id, "session_id": session_id},
        )
        row = result.mappings().one_or_none()
        return _auth_user_from_row(row) if row is not None else None

    async def touch_last_login(self, user_id: UUID, when: datetime) -> None:
        await self.session.execute(
            text("SELECT public.touch_auth_user_last_login(:user_id, :when)"),
            {"user_id": user_id, "when": when},
        )

    # -------------------------------------------------------------------------
    # email_code
    # -------------------------------------------------------------------------

    async def insert_email_code(
        self,
        *,
        email_lower: str,
        code_hash: str,
        code_salt: str,
        purpose: str,
        ip_address: str | None,
        expires_at: datetime,
    ) -> EmailCode:
        ec = EmailCode(
            email_lower=email_lower,
            code_hash=code_hash,
            code_salt=code_salt,
            purpose=purpose,
            ip_address=ip_address,
            expires_at=expires_at,
        )
        self.session.add(ec)
        await self.session.flush()
        return ec

    async def find_active_email_code(
        self, email_lower: str, purpose: str = "login"
    ) -> EmailCode | None:
        """Most-recently issued unused, unexpired code for this email+purpose."""
        now = utc_now()
        stmt = (
            select(EmailCode)
            .where(
                and_(
                    EmailCode.email_lower == email_lower,
                    EmailCode.purpose == purpose,
                    EmailCode.used_at.is_(None),
                    EmailCode.expires_at > now,
                )
            )
            .order_by(EmailCode.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_email_code_used(self, code_id: UUID, when: datetime) -> None:
        await self.session.execute(
            update(EmailCode).where(EmailCode.id == code_id).values(used_at=when)
        )

    async def delete_expired_email_codes(self, older_than: datetime) -> int:
        from sqlalchemy import delete

        result = await self.session.execute(
            delete(EmailCode).where(EmailCode.expires_at < older_than)
        )
        # DELETE/UPDATE execute returns a CursorResult at runtime; mypy infers
        # the parent Result type, hence the attr-defined ignore.
        return result.rowcount or 0  # type: ignore[attr-defined]

    async def count_codes_since(self, email_lower: str, since: datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(EmailCode)
            .where(
                and_(
                    EmailCode.email_lower == email_lower,
                    EmailCode.created_at >= since,
                )
            )
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    # -------------------------------------------------------------------------
    # login_attempt
    # -------------------------------------------------------------------------

    async def insert_login_attempt(
        self,
        *,
        email_lower: str | None,
        user_id: UUID | None,
        ip_address: str,
        user_agent: str | None,
        outcome: str,
        metadata_json: dict[str, Any] | None = None,
    ) -> None:
        attempt = LoginAttempt(
            email_lower=email_lower,
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            outcome=outcome,
            metadata_json=metadata_json,
        )
        self.session.add(attempt)
        await self.session.flush()

    async def count_recent_failures(
        self,
        *,
        email_lower: str,
        ip_address: str,
        within: timedelta,
        failure_outcomes: tuple[str, ...] = (
            "code_failed",
            "code_expired",
            "password_failed",
            "totp_failed",
        ),
    ) -> int:
        since = utc_now() - within
        stmt = (
            select(func.count())
            .select_from(LoginAttempt)
            .where(
                and_(
                    LoginAttempt.email_lower == email_lower,
                    LoginAttempt.ip_address == ip_address,
                    LoginAttempt.outcome.in_(failure_outcomes),
                    LoginAttempt.created_at >= since,
                )
            )
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def is_currently_blocked(
        self,
        *,
        email_lower: str,
        ip_address: str,
        block_window: timedelta,
    ) -> bool:
        """Returns True if a 'blocked' attempt was recorded within `block_window`."""
        since = utc_now() - block_window
        stmt = (
            select(func.count())
            .select_from(LoginAttempt)
            .where(
                and_(
                    LoginAttempt.email_lower == email_lower,
                    LoginAttempt.ip_address == ip_address,
                    LoginAttempt.outcome == "blocked",
                    LoginAttempt.created_at >= since,
                )
            )
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one()) > 0

    # -------------------------------------------------------------------------
    # session
    # -------------------------------------------------------------------------

    async def insert_session(
        self,
        *,
        user_id: UUID,
        refresh_token_hash: str,
        user_agent: str | None,
        ip_address: str | None,
        expires_at: datetime,
    ) -> Session:
        s = Session(
            user_id=user_id,
            refresh_token_hash=refresh_token_hash,
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=expires_at,
        )
        self.session.add(s)
        await self.session.flush()
        return s

    async def get_active_session_by_hash(self, token_hash: str) -> Session | None:
        now = utc_now()
        stmt = select(Session).where(
            and_(
                Session.refresh_token_hash == token_hash,
                Session.revoked_at.is_(None),
                Session.expires_at > now,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def revoke_session(self, session_id: UUID, *, reason: str, when: datetime) -> None:
        await self.session.execute(
            update(Session)
            .where(and_(Session.id == session_id, Session.revoked_at.is_(None)))
            .values(revoked_at=when, revoked_reason=reason)
        )

    async def revoke_session_by_hash(self, token_hash: str, *, reason: str, when: datetime) -> int:
        result = await self.session.execute(
            update(Session)
            .where(
                and_(
                    Session.refresh_token_hash == token_hash,
                    Session.revoked_at.is_(None),
                )
            )
            .values(revoked_at=when, revoked_reason=reason)
        )
        return result.rowcount or 0  # type: ignore[attr-defined]

    async def touch_session_last_used(self, session_id: UUID, when: datetime) -> None:
        await self.session.execute(
            update(Session).where(Session.id == session_id).values(last_used_at=when)
        )

    async def delete_expired_sessions(self, older_than: datetime) -> int:
        from sqlalchemy import delete

        result = await self.session.execute(delete(Session).where(Session.expires_at < older_than))
        return result.rowcount or 0  # type: ignore[attr-defined]
