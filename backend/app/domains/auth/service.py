"""Business logic for the auth domain.

Login lockout state lives in the `login_attempt` table. MFA additionally uses
an account-wide Redis attempt budget so changing IP addresses cannot bypass
the five-attempt limit. Codes and refresh tokens are stored only as hashes;
plaintext is returned to the client exactly once.

Future hook-points marked `TODO(roles)` will pull permission-related signals
once the roles domain (migration 0004) is on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from ipaddress import ip_address, ip_network
from typing import TYPE_CHECKING, Literal, NamedTuple, cast
from uuid import UUID

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
)
from app.core.security import (
    build_totp_uri,
    create_access_token,
    derive_mfa_encryption_key,
    derive_rotated_refresh_token,
    generate_code_salt,
    generate_device_id,
    generate_email_code,
    generate_recovery_codes,
    generate_refresh_token,
    generate_totp_secret,
    hash_code,
    hash_recovery_code,
    hash_token,
    match_totp_counter,
    mfa_encryption_keyring_json,
    verify_password,
)
from app.core.time import utc_now
from app.domains.auth.repository import (
    ActiveSessionRecord,
    AuthRepository,
    EmailCodeIssueStatus,
)

if TYPE_CHECKING:
    from app.domains.auth.repository import (
        AuthUserRecord,
        MfaChallengeRecord,
        MfaSessionRecord,
    )

logger = structlog.get_logger("auth.service")
settings = get_settings()


LOGIN_BLOCK_DURATION = timedelta(minutes=15)
MFA_CHALLENGE_DURATION = timedelta(minutes=5)
MFA_ATTEMPT_LIMIT = 5
MFA_ATTEMPT_WINDOW_SECONDS = 15 * 60


def _mask_session_ip(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = ip_address(value)
    except ValueError:
        return None
    if parsed.version == 4:
        first, second, _, _ = str(parsed).split(".")
        return f"{first}.{second}.x.x"
    network = ip_network(f"{parsed}/64", strict=False)
    return f"{network.network_address.compressed}/64"


class AuthTokens(NamedTuple):
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True)
class MfaLoginChallenge:
    status: Literal[
        "mfa_required",
        "mfa_enrollment_required",
        "mfa_recovery_required",
    ]
    challenge_token: str
    expires_in: int


@dataclass(frozen=True)
class MfaEnrollmentSetup:
    secret: str
    provisioning_uri: str
    recovery_codes: list[str]
    expires_in: int


def _mfa_challenge_status(
    purpose: str,
) -> Literal[
    "mfa_required",
    "mfa_enrollment_required",
    "mfa_recovery_required",
]:
    if purpose == "verify":
        return "mfa_required"
    if purpose == "enroll":
        return "mfa_enrollment_required"
    if purpose == "recover":
        return "mfa_recovery_required"
    raise AuthenticationError("Invalid MFA challenge")


class AuthService:
    def __init__(
        self,
        repo: AuthRepository,
        redis: Redis | None = None,
        *,
        login_guard_enabled: bool | None = None,
    ) -> None:
        self.repo = repo
        self.redis = redis
        self.login_guard_enabled = (
            settings.auth_login_guard_enabled
            if login_guard_enabled is None
            else login_guard_enabled
        )

    async def _login_is_blocked(self, *, email_lower: str, ip_address: str) -> bool:
        if not self.login_guard_enabled:
            return False
        return await self.repo.enforce_login_guard(
            email_lower=email_lower,
            ip_address=ip_address,
        )

    def _mfa_attempt_key(self, user_id: UUID) -> str:
        return f"auth:mfa-attempts:{user_id}"

    async def _claim_mfa_attempt(self, user_id: UUID) -> None:
        if self.redis is None:
            return
        try:
            pipeline = self.redis.pipeline(transaction=True)
            pipeline.incr(self._mfa_attempt_key(user_id))
            pipeline.expire(
                self._mfa_attempt_key(user_id),
                MFA_ATTEMPT_WINDOW_SECONDS,
            )
            results = cast(list[object], await pipeline.execute())
            attempts = int(cast(int, results[0]))
        except RedisError as exc:
            raise ServiceUnavailableError("MFA attempt guard is unavailable") from exc
        if attempts > MFA_ATTEMPT_LIMIT:
            raise RateLimitError(
                "Too many MFA attempts. Try again later.",
                details={
                    "retry_after_minutes": MFA_ATTEMPT_WINDOW_SECONDS // 60,
                },
            )

    async def _clear_mfa_attempts(self, user_id: UUID) -> None:
        if self.redis is None:
            return
        try:
            await self.redis.delete(self._mfa_attempt_key(user_id))
        except RedisError as exc:
            raise ServiceUnavailableError("MFA attempt guard is unavailable") from exc

    async def _register_login_device(
        self,
        *,
        session_id: UUID,
        refresh_token: str,
        device_id: str | None,
    ) -> None:
        status = await self.repo.register_session_device(
            session_id=session_id,
            refresh_token_hash=hash_token(refresh_token),
            device_id_hash=hash_token(device_id or generate_device_id()),
        )
        if status not in {"baseline", "known_device", "new_device"}:
            raise RuntimeError("Authentication device registration failed")
        if status == "new_device":
            logger.warning(
                "new_login_device_detected",
                session_id=str(session_id),
            )

    # -------------------------------------------------------------------------
    # 1. Request an email code
    # -------------------------------------------------------------------------

    async def request_login_code(
        self,
        *,
        email: str,
        ip_address: str,
        user_agent: str | None = None,
    ) -> str:
        """Returns the freshly minted plaintext code. The router decides
        whether to leak it back to the caller (dev only) or keep it secret."""
        email_lower = email.strip().lower()

        code = generate_email_code()
        salt = generate_code_salt()
        issue_status = await self.repo.issue_login_email_code(
            email_lower=email_lower,
            code_hash=hash_code(code, salt),
            code_salt=salt,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        if issue_status is EmailCodeIssueStatus.RATE_LIMIT_MINUTE:
            raise RateLimitError("Too many code requests. Try again in a minute.")
        if issue_status is EmailCodeIssueStatus.RATE_LIMIT_HOUR:
            raise RateLimitError("Too many code requests. Try again in an hour.")

        # Anti-enumeration: dispatch the email even when the user does not
        # exist (the worker will silently drop it; clients see no difference).
        # In phase 1 there is no real SMTP yet; the router returns dev_code in
        # development only. Lazy import avoids a circular dependency through
        # app.tasks.celery_app on first module load.
        from app.tasks.auth import send_email_code

        send_email_code.delay(email_lower, code)
        logger.info("login_code_issued")
        return code

    # -------------------------------------------------------------------------
    # 2. Verify an email code → issue tokens
    # -------------------------------------------------------------------------

    async def verify_login_code(
        self,
        *,
        email: str,
        code: str,
        password: str | None,
        ip_address: str,
        user_agent: str | None = None,
        device_id: str | None = None,
    ) -> AuthTokens | MfaLoginChallenge:
        email_lower = email.strip().lower()
        now = utc_now()

        if await self._login_is_blocked(email_lower=email_lower, ip_address=ip_address):
            raise RateLimitError(
                "Account temporarily locked. Try again later.",
                details={"retry_after_minutes": int(LOGIN_BLOCK_DURATION.total_seconds() // 60)},
            )

        ec = await self.repo.find_active_email_code(email_lower)
        if ec is None:
            await self.repo.insert_login_attempt(
                email_lower=email_lower,
                user_id=None,
                ip_address=ip_address,
                user_agent=user_agent,
                outcome="code_expired",
            )
            raise AuthenticationError("Invalid or expired code")

        candidate_hash = hash_code(code, ec.code_salt)
        if not await self.repo.email_code_matches(
            code_id=ec.id,
            email_lower=email_lower,
            candidate_hash=candidate_hash,
        ):
            await self.repo.insert_login_attempt(
                email_lower=email_lower,
                user_id=None,
                ip_address=ip_address,
                user_agent=user_agent,
                outcome="code_failed",
            )
            raise AuthenticationError("Invalid or expired code")

        user = await self.repo.get_login_user_by_email(
            email=email_lower,
            code_id=ec.id,
            candidate_hash=candidate_hash,
        )
        membership_can_login = user is not None and (
            user.home_tenant_id is None or user.membership_status in ("pending", "active")
        )
        if user is None or user.status not in ("invited", "active") or not membership_can_login:
            await self.repo.consume_email_code(
                code_id=ec.id,
                email_lower=email_lower,
                candidate_hash=candidate_hash,
            )
            await self.repo.insert_login_attempt(
                email_lower=email_lower,
                user_id=None,
                ip_address=ip_address,
                user_agent=user_agent,
                outcome="code_failed",
                reason="user_missing_or_inactive",
            )
            raise NotFoundError("User does not exist")

        # The database lookup resolves assignment.password_required without a
        # tenant GUC because login happens before an authenticated context exists.
        is_support = user.is_developer or user.is_administrator
        needs_password = is_support or bool(user.password_hash) or user.password_required
        if needs_password:
            password_ok = bool(
                password and user.password_hash and verify_password(password, user.password_hash)
            )
            if not password_ok:
                await self.repo.insert_login_attempt(
                    email_lower=email_lower,
                    user_id=user.id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    outcome="password_failed",
                )
                raise AuthenticationError("Invalid credentials")

        if is_support:
            challenge_token = generate_refresh_token()
            challenge = await self.repo.create_mfa_challenge_from_email_code(
                email_lower=email_lower,
                code_id=ec.id,
                candidate_hash=candidate_hash,
                token_hash=hash_token(challenge_token),
                ip_address=ip_address,
                user_agent=user_agent,
                expires_at=now + MFA_CHALLENGE_DURATION,
            )
            if challenge is None:
                raise AuthenticationError("Invalid or expired code")
            return MfaLoginChallenge(
                status=_mfa_challenge_status(challenge.purpose),
                challenge_token=challenge_token,
                expires_in=int(MFA_CHALLENGE_DURATION.total_seconds()),
            )

        refresh_token = generate_refresh_token()
        session_id = await self.repo.create_session_from_email_code(
            code_id=ec.id,
            email_lower=email_lower,
            candidate_hash=candidate_hash,
            refresh_token_hash=hash_token(refresh_token),
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=now + timedelta(days=settings.REFRESH_TOKEN_DAYS),
        )
        if session_id is None:
            raise AuthenticationError("Invalid or expired code")

        await self.repo.accept_tenant_invitation(
            session_id=session_id,
            tenant_id=user.home_tenant_id,
            accepted_at=now,
        )
        await self._register_login_device(
            session_id=session_id,
            refresh_token=refresh_token,
            device_id=device_id,
        )
        access_token = create_access_token(
            user.id,
            tenant_id=(None if user.is_developer or user.is_administrator else user.home_tenant_id),
            is_developer=user.is_developer,
            is_administrator=user.is_administrator,
            session_id=session_id,
        )
        await self.repo.touch_last_login(user.id, session_id)
        await self.repo.insert_login_attempt(
            email_lower=email_lower,
            user_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
            outcome="success",
        )
        logger.info("login_success", user_id=str(user.id))
        return AuthTokens(
            access_token,
            refresh_token,
            settings.ACCESS_TOKEN_MINUTES * 60,
        )

    # -------------------------------------------------------------------------
    # 3. Support MFA
    # -------------------------------------------------------------------------

    async def _get_mfa_challenge(
        self,
        *,
        challenge_token: str,
        ip_address: str,
        include_secret: bool = True,
    ) -> MfaChallengeRecord:
        challenge = await self.repo.get_mfa_challenge(
            token_hash=hash_token(challenge_token),
            encryption_keyring=mfa_encryption_keyring_json(),
            include_secret=include_secret,
        )
        if challenge is None:
            raise AuthenticationError("Invalid or expired MFA challenge")
        if await self._login_is_blocked(
            email_lower=challenge.email.lower(),
            ip_address=ip_address,
        ):
            raise RateLimitError(
                "Account temporarily locked. Try again later.",
                details={"retry_after_minutes": int(LOGIN_BLOCK_DURATION.total_seconds() // 60)},
            )
        return challenge

    async def start_mfa_enrollment(
        self,
        *,
        challenge_token: str,
        ip_address: str,
    ) -> MfaEnrollmentSetup:
        challenge = await self._get_mfa_challenge(
            challenge_token=challenge_token,
            ip_address=ip_address,
            include_secret=False,
        )
        if challenge.purpose not in ("enroll", "recovery_enroll"):
            raise AuthenticationError("MFA enrollment is not available")

        secret = generate_totp_secret()
        recovery_codes = generate_recovery_codes()
        staged = await self.repo.stage_mfa_enrollment(
            token_hash=hash_token(challenge_token),
            secret=secret,
            key_version=settings.MFA_ENCRYPTION_KEY_VERSION,
            encryption_key=derive_mfa_encryption_key(),
            recovery_code_hashes=[hash_recovery_code(code) for code in recovery_codes],
        )
        if not staged:
            raise AuthenticationError("Invalid or expired MFA challenge")
        return MfaEnrollmentSetup(
            secret=secret,
            provisioning_uri=build_totp_uri(
                account_name=challenge.email,
                secret=secret,
                issuer=settings.APP_NAME,
            ),
            recovery_codes=recovery_codes,
            expires_in=max(0, int((challenge.expires_at - utc_now()).total_seconds())),
        )

    async def _finalize_mfa_login(
        self,
        *,
        challenge_email: str,
        session_record: MfaSessionRecord,
        refresh_token: str,
        ip_address: str,
        user_agent: str | None,
        device_id: str | None,
    ) -> AuthTokens:
        await self._register_login_device(
            session_id=session_record.session_id,
            refresh_token=refresh_token,
            device_id=device_id,
        )
        access_token = create_access_token(
            session_record.user_id,
            tenant_id=None,
            is_developer=session_record.is_developer,
            is_administrator=session_record.is_administrator,
            session_id=session_record.session_id,
            mfa_verified_at=session_record.mfa_verified_at,
        )
        await self.repo.touch_last_login(
            session_record.user_id,
            session_record.session_id,
        )
        await self.repo.insert_login_attempt(
            email_lower=challenge_email.lower(),
            user_id=session_record.user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            outcome="success",
        )
        logger.info(
            "support_login_success",
            user_id=str(session_record.user_id),
            session_id=str(session_record.session_id),
        )
        return AuthTokens(
            access_token,
            refresh_token,
            settings.ACCESS_TOKEN_MINUTES * 60,
        )

    async def complete_mfa_enrollment(
        self,
        *,
        challenge_token: str,
        code: str,
        ip_address: str,
        user_agent: str | None = None,
        device_id: str | None = None,
    ) -> AuthTokens:
        challenge = await self._get_mfa_challenge(
            challenge_token=challenge_token,
            ip_address=ip_address,
        )
        if challenge.purpose not in ("enroll", "recovery_enroll") or not challenge.secret:
            raise AuthenticationError("MFA enrollment is not ready")

        await self._claim_mfa_attempt(challenge.user_id)
        counter = match_totp_counter(challenge.secret, code)
        if counter is None:
            await self.repo.record_mfa_failure(
                token_hash=hash_token(challenge_token),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            raise AuthenticationError("Invalid authentication code")

        refresh_token = generate_refresh_token()
        session_record = await self.repo.complete_mfa_enrollment(
            token_hash=hash_token(challenge_token),
            counter=counter,
            verified_secret=challenge.secret,
            encryption_keyring=mfa_encryption_keyring_json(),
            refresh_token_hash=hash_token(refresh_token),
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=utc_now() + timedelta(days=settings.REFRESH_TOKEN_DAYS),
        )
        if session_record is None:
            raise AuthenticationError("Invalid or expired MFA challenge")
        await self._clear_mfa_attempts(challenge.user_id)
        return await self._finalize_mfa_login(
            challenge_email=challenge.email,
            session_record=session_record,
            refresh_token=refresh_token,
            ip_address=ip_address,
            user_agent=user_agent,
            device_id=device_id,
        )

    async def verify_mfa(
        self,
        *,
        challenge_token: str,
        code: str,
        ip_address: str,
        user_agent: str | None = None,
        device_id: str | None = None,
    ) -> AuthTokens:
        challenge = await self._get_mfa_challenge(
            challenge_token=challenge_token,
            ip_address=ip_address,
        )
        if challenge.purpose != "verify" or not challenge.secret:
            raise AuthenticationError("MFA verification is not available")

        await self._claim_mfa_attempt(challenge.user_id)
        counter = match_totp_counter(
            challenge.secret,
            code,
            last_used_counter=challenge.last_used_counter,
        )
        if counter is None:
            await self.repo.record_mfa_failure(
                token_hash=hash_token(challenge_token),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            raise AuthenticationError("Invalid authentication code")

        refresh_token = generate_refresh_token()
        session_record = await self.repo.complete_mfa_verification(
            token_hash=hash_token(challenge_token),
            counter=counter,
            verified_secret=challenge.secret,
            encryption_keyring=mfa_encryption_keyring_json(),
            refresh_token_hash=hash_token(refresh_token),
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=utc_now() + timedelta(days=settings.REFRESH_TOKEN_DAYS),
        )
        if session_record is None:
            raise AuthenticationError("Invalid or replayed authentication code")
        await self._clear_mfa_attempts(challenge.user_id)
        return await self._finalize_mfa_login(
            challenge_email=challenge.email,
            session_record=session_record,
            refresh_token=refresh_token,
            ip_address=ip_address,
            user_agent=user_agent,
            device_id=device_id,
        )

    async def recover_mfa(
        self,
        *,
        challenge_token: str,
        recovery_code: str,
        ip_address: str,
        user_agent: str | None = None,
    ) -> MfaLoginChallenge:
        challenge = await self._get_mfa_challenge(
            challenge_token=challenge_token,
            ip_address=ip_address,
            include_secret=False,
        )
        if challenge.purpose not in ("verify", "recover"):
            raise AuthenticationError("MFA recovery is not available")
        await self._claim_mfa_attempt(challenge.user_id)
        try:
            recovery_code_hash = hash_recovery_code(recovery_code)
        except ValueError:
            recovery_code_hash = hash_token(f"invalid-recovery:{recovery_code}")

        recovered = await self.repo.recover_mfa_challenge(
            token_hash=hash_token(challenge_token),
            recovery_code_hash=recovery_code_hash,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        if not recovered:
            raise AuthenticationError("Invalid recovery code")
        await self._clear_mfa_attempts(challenge.user_id)
        return MfaLoginChallenge(
            status="mfa_enrollment_required",
            challenge_token=challenge_token,
            expires_in=10 * 60,
        )

    async def step_up_mfa(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        is_developer: bool,
        is_administrator: bool,
        code: str,
        ip_address: str,
        user_agent: str | None = None,
    ) -> tuple[str, int]:
        if not (is_developer or is_administrator):
            raise AuthenticationError("Support MFA is required")
        record = await self.repo.get_step_up_mfa(
            user_id=user_id,
            session_id=session_id,
            encryption_keyring=mfa_encryption_keyring_json(),
        )
        if record is None:
            raise AuthenticationError("Support MFA is unavailable")
        if await self._login_is_blocked(
            email_lower=record.email.lower(),
            ip_address=ip_address,
        ):
            raise RateLimitError(
                "Account temporarily locked. Try again later.",
                details={"retry_after_minutes": int(LOGIN_BLOCK_DURATION.total_seconds() // 60)},
            )
        await self._claim_mfa_attempt(user_id)
        counter = match_totp_counter(
            record.secret,
            code,
            last_used_counter=record.last_used_counter,
        )
        if counter is None:
            await self.repo.insert_login_attempt(
                email_lower=record.email.lower(),
                user_id=user_id,
                ip_address=ip_address,
                user_agent=user_agent,
                outcome="totp_failed",
            )
            raise AuthenticationError("Invalid authentication code")

        verified_at = await self.repo.complete_step_up_mfa(
            user_id=user_id,
            session_id=session_id,
            counter=counter,
            verified_secret=record.secret,
            encryption_keyring=mfa_encryption_keyring_json(),
        )
        if verified_at is None:
            raise AuthenticationError("Invalid or replayed authentication code")
        await self._clear_mfa_attempts(user_id)
        access_token = create_access_token(
            user_id,
            tenant_id=None,
            is_developer=is_developer,
            is_administrator=is_administrator,
            session_id=session_id,
            mfa_verified_at=verified_at,
        )
        return access_token, settings.ACCESS_TOKEN_MINUTES * 60

    # -------------------------------------------------------------------------
    # 4. Refresh
    # -------------------------------------------------------------------------

    async def refresh(
        self,
        *,
        refresh_token: str,
        operation_id: UUID,
        ip_address: str,
        user_agent: str | None = None,
    ) -> AuthTokens:
        token_hash = hash_token(refresh_token)
        new_refresh_token = derive_rotated_refresh_token(refresh_token, operation_id)
        rotated = await self.repo.rotate_session(
            old_token_hash=token_hash,
            new_token_hash=hash_token(new_refresh_token),
            operation_id=operation_id,
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=utc_now() + timedelta(days=settings.REFRESH_TOKEN_DAYS),
        )
        if rotated is None:
            raise AuthenticationError("Invalid or expired refresh token")

        user = await self.repo.get_user_by_id(rotated.user_id, session_id=rotated.id)
        membership_is_active = user is not None and (
            user.home_tenant_id is None or user.membership_status == "active"
        )
        if user is None or user.status not in ("invited", "active") or not membership_is_active:
            rotated_token = refresh_token if rotated.reuse_presented_token else new_refresh_token
            await self.repo.revoke_session_by_hash(
                hash_token(rotated_token),
                reason="user_inactive",
            )
            raise AuthenticationError("Invalid or expired refresh token")

        mfa_verified_at = await self.repo.get_session_mfa_verified_at(
            session_id=rotated.id,
            user_id=user.id,
        )
        if (user.is_developer or user.is_administrator) and (
            user.mfa_status != "active" or mfa_verified_at is None
        ):
            raise AuthenticationError("Support MFA is required")

        access_token = create_access_token(
            user.id,
            tenant_id=(None if user.is_developer or user.is_administrator else user.home_tenant_id),
            is_developer=user.is_developer,
            is_administrator=user.is_administrator,
            session_id=rotated.id,
            mfa_verified_at=mfa_verified_at,
        )
        result_refresh_token = refresh_token if rotated.reuse_presented_token else new_refresh_token
        logger.info("refresh_rotated", user_id=str(user.id), session_id=str(rotated.id))
        return AuthTokens(
            access_token,
            result_refresh_token,
            settings.ACCESS_TOKEN_MINUTES * 60,
        )

    # -------------------------------------------------------------------------
    # 5. Session inventory and revocation
    # -------------------------------------------------------------------------

    async def list_sessions(
        self,
        *,
        user_id: UUID,
        current_session_id: UUID | None,
    ) -> list[ActiveSessionRecord]:
        sessions = await self.repo.list_active_sessions(
            user_id=user_id,
            current_session_id=current_session_id,
        )
        return [
            replace(session, ip_address=_mask_session_ip(session.ip_address))
            for session in sessions
        ]

    async def revoke_session(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        current_session_id: UUID | None,
    ) -> None:
        if current_session_id is None:
            raise AuthenticationError("Authenticated session is missing")
        result = await self.repo.revoke_session_by_id(
            user_id=user_id,
            session_id=session_id,
            current_session_id=current_session_id,
        )
        if result == "current":
            raise ConflictError("Current session must be ended with logout")
        if result == "not_found":
            raise NotFoundError("Session not found")
        if result != "revoked":
            raise RuntimeError("Unexpected session revocation result")
        logger.info(
            "session_revoked",
            user_id=str(user_id),
            session_id=str(session_id),
        )

    async def revoke_other_sessions(
        self,
        *,
        user_id: UUID,
        current_session_id: UUID | None,
    ) -> int:
        if current_session_id is None:
            raise AuthenticationError("Authenticated session is missing")
        revoked_count = await self.repo.revoke_other_sessions(
            user_id=user_id,
            current_session_id=current_session_id,
        )
        logger.info(
            "other_sessions_revoked",
            user_id=str(user_id),
            session_id=str(current_session_id),
            revoked_count=revoked_count,
        )
        return revoked_count

    # -------------------------------------------------------------------------
    # 6. Logout (idempotent)
    # -------------------------------------------------------------------------

    async def logout(self, refresh_token: str, operation_id: UUID | None = None) -> None:
        token_hash = hash_token(refresh_token)
        user_id = await self.repo.revoke_session_by_hash(
            token_hash,
            reason="logout",
            operation_id=operation_id,
        )
        if user_id is not None and self.redis is not None:
            # Local import — RolesService pulls from roles domain, which
            # imports auth at module level. Lazy keeps the load DAG acyclic.
            from app.domains.roles.repository import RolesRepository
            from app.domains.roles.service import RolesService

            roles_service = RolesService(RolesRepository(self.repo.session), redis=self.redis)
            await roles_service.invalidate_user_perms_all_tenants(user_id)
        if user_id is not None:
            logger.info("logout", user_id=str(user_id))

    # -------------------------------------------------------------------------
    # 7. /me
    # -------------------------------------------------------------------------

    async def get_user_info(self, user_id: UUID) -> AuthUserRecord:
        user = await self.repo.get_user_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found")
        return user
