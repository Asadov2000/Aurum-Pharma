"""Database access for the auth domain. No business logic here."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import cast
from uuid import UUID

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.auth.models import EmailCode, Session, UserPreferences


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
    mfa_status: str | None
    mfa_required: bool


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


@dataclass(frozen=True)
class ActiveSessionRecord:
    id: UUID
    user_agent: str | None
    ip_address: str | None
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    is_current: bool


@dataclass(frozen=True)
class MfaChallengeCreated:
    id: UUID
    purpose: str


@dataclass(frozen=True)
class MfaChallengeRecord:
    user_id: UUID
    email: str
    is_developer: bool
    is_administrator: bool
    purpose: str
    mfa_status: str | None
    secret: str | None
    last_used_counter: int | None
    failed_attempts: int
    expires_at: datetime


@dataclass(frozen=True)
class MfaSessionRecord:
    session_id: UUID
    user_id: UUID
    mfa_verified_at: datetime
    is_developer: bool
    is_administrator: bool


@dataclass(frozen=True)
class StepUpMfaRecord:
    email: str
    secret: str
    last_used_counter: int | None


@dataclass(frozen=True)
class MfaSettingsRecord:
    status: str | None
    prompt_dismissed_at: datetime | None
    password_configured: bool


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
        mfa_status=cast(str | None, row["mfa_status"]),
        mfa_required=bool(
            row.get(
                "mfa_required",
                row["mfa_status"] in {"active", "recovery_pending"},
            )
        ),
    )


def _auth_session_from_row(row: RowMapping) -> AuthSessionRecord:
    return AuthSessionRecord(
        id=cast(UUID, row["id"]),
        user_id=cast(UUID, row["user_id"]),
        expires_at=cast(datetime, row["expires_at"]),
        reuse_presented_token=bool(row["reuse_presented_token"]),
    )


def _active_session_from_row(row: RowMapping) -> ActiveSessionRecord:
    return ActiveSessionRecord(
        id=cast(UUID, row["id"]),
        user_agent=cast(str | None, row["user_agent"]),
        ip_address=cast(str | None, row["ip_address"]),
        created_at=cast(datetime, row["created_at"]),
        last_used_at=cast(datetime, row["last_used_at"]),
        expires_at=cast(datetime, row["expires_at"]),
        is_current=bool(row["is_current"]),
    )


def _is_insufficient_privilege(error: DBAPIError) -> bool:
    sqlstate = getattr(error.orig, "sqlstate", None)
    return isinstance(sqlstate, str) and sqlstate == "42501"


def _mfa_challenge_from_row(row: RowMapping) -> MfaChallengeRecord:
    return MfaChallengeRecord(
        user_id=cast(UUID, row["user_id"]),
        email=cast(str, row["email"]),
        is_developer=bool(row["is_developer"]),
        is_administrator=bool(row["is_administrator"]),
        purpose=cast(str, row["purpose"]),
        mfa_status=cast(str | None, row["mfa_status"]),
        secret=cast(str | None, row["secret"]),
        last_used_counter=cast(int | None, row["last_used_counter"]),
        failed_attempts=int(row["failed_attempts"]),
        expires_at=cast(datetime, row["expires_at"]),
    )


def _mfa_session_from_row(row: RowMapping) -> MfaSessionRecord:
    return MfaSessionRecord(
        session_id=cast(UUID, row["session_id"]),
        user_id=cast(UUID, row["user_id"]),
        mfa_verified_at=cast(datetime, row["mfa_verified_at"]),
        is_developer=bool(row["is_developer"]),
        is_administrator=bool(row["is_administrator"]),
    )


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create_user_preferences(self, user_id: UUID) -> UserPreferences:
        await self.session.execute(
            insert(UserPreferences)
            .values(user_id=user_id)
            .on_conflict_do_nothing(index_elements=[UserPreferences.user_id])
        )
        preferences = await self.session.scalar(
            select(UserPreferences)
            .where(UserPreferences.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        if preferences is None:
            raise RuntimeError("User preferences are unavailable")
        return preferences

    async def update_user_preferences(
        self,
        *,
        user_id: UUID,
        expected_version: int,
        fields: dict[str, object],
    ) -> UserPreferences | None:
        result = await self.session.execute(
            update(UserPreferences)
            .where(
                UserPreferences.user_id == user_id,
                UserPreferences.version == expected_version,
            )
            .values(**fields, version=UserPreferences.version + 1)
            .returning(UserPreferences)
        )
        return result.scalar_one_or_none()

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
        try:
            result = await self.session.execute(
                text("SELECT * FROM public.lookup_auth_user_by_id(:user_id, :session_id)"),
                {"user_id": user_id, "session_id": session_id},
            )
            row = result.mappings().one_or_none()
            if row is None:
                return None
            requirement = await self.session.scalar(
                text("SELECT public.lookup_auth_account_mfa_requirement(:user_id, :session_id)"),
                {"user_id": user_id, "session_id": session_id},
            )
            if requirement is None:
                return None
        except DBAPIError as error:
            # A revoked/expired sid is deliberately rejected inside the
            # SECURITY DEFINER function. At the auth boundary it is an invalid
            # session, not an internal server failure.
            if _is_insufficient_privilege(error):
                return None
            raise
        return replace(_auth_user_from_row(row), mfa_required=bool(requirement))

    async def get_active_platform_capabilities(
        self,
        user_id: UUID,
        session_id: UUID,
    ) -> frozenset[str]:
        result = await self.session.scalars(
            text("""
                SELECT capability.code
                FROM public.lookup_active_platform_capabilities(
                  :user_id,
                  :session_id
                )
                  AS capability
                """),
            {"user_id": user_id, "session_id": session_id},
        )
        return frozenset(str(code) for code in result)

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
        plaintext_code: str,
        encryption_key_version: int,
        encryption_key: str,
        ip_address: str,
        user_agent: str | None,
    ) -> EmailCodeIssueStatus:
        result = await self.session.execute(
            text(
                "SELECT public.issue_auth_email_code("
                ":email, :code_hash, :code_salt, :ip_address, :user_agent, "
                ":plaintext_code, CAST(:encryption_key_version AS SMALLINT), "
                ":encryption_key)"
            ),
            {
                "email": email_lower,
                "code_hash": code_hash,
                "code_salt": code_salt,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "plaintext_code": plaintext_code,
                "encryption_key_version": encryption_key_version,
                "encryption_key": encryption_key,
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
    # support MFA
    # -------------------------------------------------------------------------

    async def create_mfa_challenge_from_email_code(
        self,
        *,
        email_lower: str,
        code_id: UUID,
        candidate_hash: str,
        token_hash: str,
        ip_address: str,
        user_agent: str | None,
        expires_at: datetime,
    ) -> MfaChallengeCreated | None:
        result = await self.session.execute(
            text(
                "SELECT * FROM public.create_auth_mfa_challenge_from_email_code("
                ":email, :code_id, :candidate_hash, :token_hash, :ip_address, "
                ":user_agent, :expires_at)"
            ),
            {
                "email": email_lower,
                "code_id": code_id,
                "candidate_hash": candidate_hash,
                "token_hash": token_hash,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "expires_at": expires_at,
            },
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return MfaChallengeCreated(
            id=cast(UUID, row["challenge_id"]),
            purpose=cast(str, row["purpose"]),
        )

    async def get_mfa_challenge(
        self,
        *,
        token_hash: str,
        encryption_keyring: str,
        include_secret: bool,
    ) -> MfaChallengeRecord | None:
        result = await self.session.execute(
            text(
                "SELECT * FROM public.lookup_auth_mfa_challenge("
                ":token_hash, CAST(:encryption_keyring AS JSONB), :include_secret)"
            ),
            {
                "token_hash": token_hash,
                "encryption_keyring": encryption_keyring,
                "include_secret": include_secret,
            },
        )
        row = result.mappings().one_or_none()
        return _mfa_challenge_from_row(row) if row is not None else None

    async def stage_mfa_enrollment(
        self,
        *,
        token_hash: str,
        secret: str,
        key_version: int,
        encryption_key: str,
        recovery_code_hashes: list[str],
    ) -> bool:
        result = await self.session.execute(
            text(
                "SELECT public.stage_auth_mfa_enrollment("
                ":token_hash, :secret, :key_version, :encryption_key, :code_hashes)"
            ),
            {
                "token_hash": token_hash,
                "secret": secret,
                "key_version": key_version,
                "encryption_key": encryption_key,
                "code_hashes": recovery_code_hashes,
            },
        )
        return bool(result.scalar_one())

    async def complete_mfa_enrollment(
        self,
        *,
        token_hash: str,
        counter: int,
        verified_secret: str,
        encryption_keyring: str,
        refresh_token_hash: str,
        user_agent: str | None,
        ip_address: str,
        expires_at: datetime,
    ) -> MfaSessionRecord | None:
        result = await self.session.execute(
            text(
                "SELECT * FROM public.complete_auth_mfa_enrollment("
                ":token_hash, :counter, :verified_secret, "
                "CAST(:encryption_keyring AS JSONB), "
                ":refresh_token_hash, :user_agent, :ip_address, :expires_at)"
            ),
            {
                "token_hash": token_hash,
                "counter": counter,
                "verified_secret": verified_secret,
                "encryption_keyring": encryption_keyring,
                "refresh_token_hash": refresh_token_hash,
                "user_agent": user_agent,
                "ip_address": ip_address,
                "expires_at": expires_at,
            },
        )
        row = result.mappings().one_or_none()
        return _mfa_session_from_row(row) if row is not None else None

    async def complete_mfa_verification(
        self,
        *,
        token_hash: str,
        counter: int,
        verified_secret: str,
        encryption_keyring: str,
        refresh_token_hash: str,
        user_agent: str | None,
        ip_address: str,
        expires_at: datetime,
    ) -> MfaSessionRecord | None:
        result = await self.session.execute(
            text(
                "SELECT * FROM public.complete_auth_mfa_verification("
                ":token_hash, :counter, :verified_secret, "
                "CAST(:encryption_keyring AS JSONB), "
                ":refresh_token_hash, :user_agent, :ip_address, :expires_at)"
            ),
            {
                "token_hash": token_hash,
                "counter": counter,
                "verified_secret": verified_secret,
                "encryption_keyring": encryption_keyring,
                "refresh_token_hash": refresh_token_hash,
                "user_agent": user_agent,
                "ip_address": ip_address,
                "expires_at": expires_at,
            },
        )
        row = result.mappings().one_or_none()
        return _mfa_session_from_row(row) if row is not None else None

    async def record_mfa_failure(
        self,
        *,
        token_hash: str,
        ip_address: str,
        user_agent: str | None,
    ) -> bool:
        result = await self.session.execute(
            text("SELECT public.record_auth_mfa_failure(" ":token_hash, :ip_address, :user_agent)"),
            {
                "token_hash": token_hash,
                "ip_address": ip_address,
                "user_agent": user_agent,
            },
        )
        return bool(result.scalar_one())

    async def recover_mfa_challenge(
        self,
        *,
        token_hash: str,
        recovery_code_hash: str,
        ip_address: str,
        user_agent: str | None,
    ) -> bool:
        result = await self.session.execute(
            text(
                "SELECT public.recover_auth_mfa_challenge("
                ":token_hash, :code_hash, :ip_address, :user_agent)"
            ),
            {
                "token_hash": token_hash,
                "code_hash": recovery_code_hash,
                "ip_address": ip_address,
                "user_agent": user_agent,
            },
        )
        return bool(result.scalar_one())

    async def get_step_up_mfa(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        encryption_keyring: str,
    ) -> StepUpMfaRecord | None:
        result = await self.session.execute(
            text(
                "SELECT * FROM public.lookup_support_mfa_for_step_up("
                ":user_id, :session_id, CAST(:encryption_keyring AS JSONB))"
            ),
            {
                "user_id": user_id,
                "session_id": session_id,
                "encryption_keyring": encryption_keyring,
            },
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return StepUpMfaRecord(
            email=cast(str, row["email"]),
            secret=cast(str, row["secret"]),
            last_used_counter=cast(int | None, row["last_used_counter"]),
        )

    async def complete_step_up_mfa(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        counter: int,
        verified_secret: str,
        encryption_keyring: str,
    ) -> datetime | None:
        result = await self.session.execute(
            text(
                "SELECT public.complete_support_mfa_step_up("
                ":user_id, :session_id, :counter, :verified_secret, "
                "CAST(:encryption_keyring AS JSONB))"
            ),
            {
                "user_id": user_id,
                "session_id": session_id,
                "counter": counter,
                "verified_secret": verified_secret,
                "encryption_keyring": encryption_keyring,
            },
        )
        return cast(datetime | None, result.scalar_one())

    # -------------------------------------------------------------------------
    # session
    # -------------------------------------------------------------------------

    async def list_active_sessions(
        self,
        *,
        user_id: UUID,
        current_session_id: UUID | None,
    ) -> list[ActiveSessionRecord]:
        result = await self.session.execute(
            text("SELECT * FROM public.lookup_auth_sessions(" ":user_id, :current_session_id)"),
            {
                "user_id": user_id,
                "current_session_id": current_session_id,
            },
        )
        return [_active_session_from_row(row) for row in result.mappings().all()]

    async def revoke_session_by_id(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        current_session_id: UUID,
    ) -> str:
        result = await self.session.execute(
            text(
                "SELECT public.revoke_auth_session_by_id("
                ":user_id, :session_id, :current_session_id)"
            ),
            {
                "user_id": user_id,
                "session_id": session_id,
                "current_session_id": current_session_id,
            },
        )
        return cast(str, result.scalar_one())

    async def revoke_other_sessions(
        self,
        *,
        user_id: UUID,
        current_session_id: UUID,
    ) -> int:
        result = await self.session.execute(
            text("SELECT public.revoke_other_auth_sessions(" ":user_id, :current_session_id)"),
            {
                "user_id": user_id,
                "current_session_id": current_session_id,
            },
        )
        return int(result.scalar_one())

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

    async def register_session_device(
        self,
        *,
        session_id: UUID,
        refresh_token_hash: str,
        device_id_hash: str,
    ) -> str:
        result = await self.session.execute(
            text(
                "SELECT public.register_auth_session_device("
                ":session_id, :refresh_token_hash, :device_id_hash)"
            ),
            {
                "session_id": session_id,
                "refresh_token_hash": refresh_token_hash,
                "device_id_hash": device_id_hash,
            },
        )
        return str(result.scalar_one())

    async def accept_tenant_invitation(
        self,
        *,
        session_id: UUID,
        tenant_id: UUID | None,
        email_code_id: UUID,
        accepted_at: datetime,
    ) -> int:
        result = await self.session.execute(
            text(
                "SELECT public.accept_tenant_invitation("
                ":session_id, :tenant_id, :email_code_id, :accepted_at)"
            ),
            {
                "session_id": session_id,
                "tenant_id": tenant_id,
                "email_code_id": email_code_id,
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

    async def get_session_mfa_verified_at(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
    ) -> datetime | None:
        result = await self.session.execute(
            text("SELECT public.lookup_auth_session_mfa(" ":session_id, :user_id)"),
            {
                "session_id": session_id,
                "user_id": user_id,
            },
        )
        return cast(datetime | None, result.scalar_one())

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

    async def get_mfa_settings(
        self, *, user_id: UUID, session_id: UUID
    ) -> MfaSettingsRecord | None:
        result = await self.session.execute(
            text("SELECT * FROM public.lookup_account_mfa_settings(:user_id, :session_id)"),
            {"user_id": user_id, "session_id": session_id},
        )
        row = result.mappings().one_or_none()
        return (
            MfaSettingsRecord(
                status=cast(str | None, row["status"]),
                prompt_dismissed_at=cast(datetime | None, row["prompt_dismissed_at"]),
                password_configured=bool(row["password_configured"]),
            )
            if row is not None
            else None
        )

    async def dismiss_mfa_prompt(self, *, user_id: UUID, session_id: UUID) -> bool:
        return bool(
            await self.session.scalar(
                text("SELECT public.dismiss_account_mfa_prompt(:user_id, :session_id)"),
                {"user_id": user_id, "session_id": session_id},
            )
        )

    async def get_account_password_hash(self, *, user_id: UUID, session_id: UUID) -> str | None:
        return cast(
            str | None,
            await self.session.scalar(
                text("SELECT public.lookup_account_password_hash(:user_id, :session_id)"),
                {"user_id": user_id, "session_id": session_id},
            ),
        )

    async def confirm_password(
        self, *, user_id: UUID, session_id: UUID, verified_password_hash: str
    ) -> datetime | None:
        return cast(
            datetime | None,
            await self.session.scalar(
                text(
                    "SELECT public.confirm_account_password(:user_id, :session_id, :verified_hash)"
                ),
                {
                    "user_id": user_id,
                    "session_id": session_id,
                    "verified_hash": verified_password_hash,
                },
            ),
        )

    async def get_session_password_verified_at(
        self, *, user_id: UUID, session_id: UUID
    ) -> datetime | None:
        return cast(
            datetime | None,
            await self.session.scalar(
                text("SELECT public.lookup_auth_session_password(:user_id, :session_id)"),
                {"user_id": user_id, "session_id": session_id},
            ),
        )

    async def set_account_password(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        code_id: UUID,
        candidate_hash: str,
        password_hash: str,
    ) -> datetime | None:
        return cast(
            datetime | None,
            await self.session.scalar(
                text(
                    "SELECT public.set_initial_account_password("
                    ":user_id, :session_id, :code_id, :candidate_hash, :password_hash)"
                ),
                {
                    "user_id": user_id,
                    "session_id": session_id,
                    "code_id": code_id,
                    "candidate_hash": candidate_hash,
                    "password_hash": password_hash,
                },
            ),
        )

    async def create_authenticated_mfa_challenge(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        token_hash: str,
        verified_password_hash: str,
        ip_address: str,
        user_agent: str | None,
        expires_at: datetime,
    ) -> MfaChallengeCreated | None:
        result = await self.session.execute(
            text(
                "SELECT * FROM public.create_authenticated_mfa_challenge("
                ":user_id, :session_id, :token_hash, :verified_hash, "
                ":ip_address, :user_agent, :expires_at)"
            ),
            {
                "user_id": user_id,
                "session_id": session_id,
                "token_hash": token_hash,
                "verified_hash": verified_password_hash,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "expires_at": expires_at,
            },
        )
        row = result.mappings().one_or_none()
        return (
            MfaChallengeCreated(id=cast(UUID, row["id"]), purpose=str(row["purpose"]))
            if row
            else None
        )

    async def is_authenticated_mfa_challenge(
        self, *, user_id: UUID, session_id: UUID, token_hash: str
    ) -> bool:
        return bool(
            await self.session.scalar(
                text(
                    "SELECT public.authenticated_mfa_challenge_matches("
                    ":user_id, :session_id, :token_hash)"
                ),
                {"user_id": user_id, "session_id": session_id, "token_hash": token_hash},
            )
        )

    async def disable_mfa(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        verified_password_hash: str,
        refresh_token_hash: str,
        user_agent: str | None,
        ip_address: str,
        expires_at: datetime,
    ) -> UUID | None:
        return cast(
            UUID | None,
            await self.session.scalar(
                text(
                    "SELECT public.disable_account_mfa("
                    ":user_id, :session_id, :verified_hash, :refresh_hash, "
                    ":user_agent, :ip_address, :expires_at)"
                ),
                {
                    "user_id": user_id,
                    "session_id": session_id,
                    "verified_hash": verified_password_hash,
                    "refresh_hash": refresh_token_hash,
                    "user_agent": user_agent,
                    "ip_address": ip_address,
                    "expires_at": expires_at,
                },
            ),
        )

    async def delete_expired_sessions(self, older_than: datetime) -> int:
        result = await self.session.execute(delete(Session).where(Session.expires_at < older_than))
        return result.rowcount or 0  # type: ignore[attr-defined]
