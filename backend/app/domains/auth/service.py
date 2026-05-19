"""Business logic for the auth domain.

Rate-limit + lockout state lives in the `login_attempt` table (DB, not Redis)
so a Redis restart cannot silently lift a block. Codes and refresh tokens
are stored only as hashes; plaintext is returned to the client exactly once.

Future hook-points marked `TODO(roles)` will pull permission-related signals
once the roles domain (migration 0004) is on disk.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.errors import (
    AuthenticationError,
    NotFoundError,
    RateLimitError,
)
from app.core.security import (
    create_access_token,
    generate_code_salt,
    generate_email_code,
    generate_refresh_token,
    hash_code,
    hash_token,
    verify_password,
)
from app.core.time import utc_now
from app.domains.auth.repository import AuthRepository

if TYPE_CHECKING:
    from app.domains.auth.models import AppUser

logger = structlog.get_logger("auth.service")
settings = get_settings()


# Rate-limit constants — pulled out so tests can monkeypatch if needed.
CODE_TTL = timedelta(minutes=10)
CODE_RATE_PER_MINUTE = 1
CODE_RATE_PER_HOUR = 10
LOGIN_FAIL_THRESHOLD = 5
LOGIN_FAIL_WINDOW = timedelta(minutes=15)
LOGIN_BLOCK_DURATION = timedelta(minutes=15)


class AuthService:
    def __init__(self, repo: AuthRepository, redis: Redis | None = None) -> None:
        self.repo = repo
        self.redis = redis

    # -------------------------------------------------------------------------
    # 1. Request an email code
    # -------------------------------------------------------------------------

    async def request_login_code(
        self,
        *,
        email: str,
        ip_address: str,
        user_agent: str | None = None,
    ) -> None:
        email_lower = email.lower()
        now = utc_now()

        # Rate-limit: 1 / minute and 10 / hour per email.
        count_last_minute = await self.repo.count_codes_since(
            email_lower, now - timedelta(minutes=1)
        )
        if count_last_minute >= CODE_RATE_PER_MINUTE:
            await self.repo.insert_login_attempt(
                email_lower=email_lower,
                user_id=None,
                ip_address=ip_address,
                user_agent=user_agent,
                outcome="blocked",
                metadata_json={"reason": "code_rate_limit_minute"},
            )
            raise RateLimitError("Too many code requests. Try again in a minute.")

        count_last_hour = await self.repo.count_codes_since(email_lower, now - timedelta(hours=1))
        if count_last_hour >= CODE_RATE_PER_HOUR:
            await self.repo.insert_login_attempt(
                email_lower=email_lower,
                user_id=None,
                ip_address=ip_address,
                user_agent=user_agent,
                outcome="blocked",
                metadata_json={"reason": "code_rate_limit_hour"},
            )
            raise RateLimitError("Too many code requests. Try again in an hour.")

        # Generate + store hashed.
        code = generate_email_code()
        salt = generate_code_salt()
        await self.repo.insert_email_code(
            email_lower=email_lower,
            code_hash=hash_code(code, salt),
            code_salt=salt,
            purpose="login",
            ip_address=ip_address,
            expires_at=now + CODE_TTL,
        )
        await self.repo.insert_login_attempt(
            email_lower=email_lower,
            user_id=None,
            ip_address=ip_address,
            user_agent=user_agent,
            outcome="code_requested",
        )

        # Anti-enumeration: dispatch the email even when the user does not
        # exist (the worker will silently drop it; clients see no difference).
        # In phase 1 there is no real SMTP — the Celery task just logs the code
        # via structlog. Lazy import avoids a circular dependency through
        # app.tasks.celery_app on first module load.
        from app.tasks.auth import send_email_code

        send_email_code.delay(email_lower, code)
        logger.info("login_code_issued", email=email_lower)

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
    ) -> tuple[str, str, int]:
        """Returns (access_token, refresh_token, access_expires_in_seconds)."""
        email_lower = email.lower()
        now = utc_now()

        # Hard block: if a recent 'blocked' attempt is on file, refuse outright.
        if await self.repo.is_currently_blocked(
            email_lower=email_lower,
            ip_address=ip_address,
            block_window=LOGIN_BLOCK_DURATION,
        ):
            raise RateLimitError(
                "Account temporarily locked. Try again later.",
                details={"retry_after_minutes": int(LOGIN_BLOCK_DURATION.total_seconds() // 60)},
            )

        # Soft threshold: 5 failures in 15 min → record a 'blocked' attempt
        # (which sets up the hard block above for the next 15 min).
        recent_failures = await self.repo.count_recent_failures(
            email_lower=email_lower,
            ip_address=ip_address,
            within=LOGIN_FAIL_WINDOW,
        )
        if recent_failures >= LOGIN_FAIL_THRESHOLD:
            await self.repo.insert_login_attempt(
                email_lower=email_lower,
                user_id=None,
                ip_address=ip_address,
                user_agent=user_agent,
                outcome="blocked",
                metadata_json={"reason": "too_many_failures"},
            )
            raise RateLimitError(
                "Too many failed attempts. Locked for 15 minutes.",
                details={"retry_after_minutes": 15},
            )

        # Locate the code.
        ec = await self.repo.find_active_email_code(email_lower, purpose="login")
        if ec is None:
            await self.repo.insert_login_attempt(
                email_lower=email_lower,
                user_id=None,
                ip_address=ip_address,
                user_agent=user_agent,
                outcome="code_expired",
            )
            raise AuthenticationError("Invalid or expired code")

        if ec.code_hash != hash_code(code, ec.code_salt):
            await self.repo.insert_login_attempt(
                email_lower=email_lower,
                user_id=None,
                ip_address=ip_address,
                user_agent=user_agent,
                outcome="code_failed",
            )
            raise AuthenticationError("Invalid or expired code")

        # Code is valid — does the user actually exist?
        user = await self.repo.get_user_by_email(email_lower)
        if user is None or user.status not in ("invited", "active"):
            # Burn the code so the same secret cannot be retried.
            await self.repo.mark_email_code_used(ec.id, now)
            await self.repo.insert_login_attempt(
                email_lower=email_lower,
                user_id=None,
                ip_address=ip_address,
                user_agent=user_agent,
                outcome="code_failed",
                metadata_json={"reason": "user_missing_or_inactive"},
            )
            # The schema says only support creates users in phase 1.
            raise NotFoundError("User does not exist")

        # Password check. We require a password when EITHER:
        #   1) the user has a password_hash set, OR
        #   2) any active user_assignment in the user's home tenant has
        #      password_required = true.
        needs_password = bool(user.password_hash)
        if not needs_password and user.home_tenant_id is not None:
            from app.domains.roles.repository import RolesRepository

            roles_repo = RolesRepository(self.repo.session)
            assignments = await roles_repo.list_assignments_for_user(
                user.id, tenant_id=user.home_tenant_id
            )
            needs_password = any(a.password_required and a.is_active for a in assignments)
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

        # All checks passed — burn the code, mint tokens, log success.
        await self.repo.mark_email_code_used(ec.id, now)
        access_token, refresh_token, expires_in = await self._issue_tokens(
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        await self.repo.touch_last_login(user.id, now)
        await self.repo.insert_login_attempt(
            email_lower=email_lower,
            user_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
            outcome="success",
        )
        logger.info("login_success", user_id=str(user.id))
        return access_token, refresh_token, expires_in

    # -------------------------------------------------------------------------
    # 3. Refresh
    # -------------------------------------------------------------------------

    async def refresh(
        self,
        *,
        refresh_token: str,
        ip_address: str,
        user_agent: str | None = None,
    ) -> tuple[str, str, int]:
        now = utc_now()
        token_hash = hash_token(refresh_token)
        s = await self.repo.get_active_session_by_hash(token_hash)
        if s is None:
            raise AuthenticationError("Invalid or expired refresh token")

        user = await self.repo.get_user_by_id(s.user_id)
        if user is None or user.status not in ("invited", "active"):
            await self.repo.revoke_session(s.id, reason="user_inactive", when=now)
            raise AuthenticationError("Invalid or expired refresh token")

        # Rotation: revoke the presented session, issue a fresh one.
        await self.repo.revoke_session(s.id, reason="rotated", when=now)
        access_token, new_refresh_token, expires_in = await self._issue_tokens(
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        logger.info("refresh_rotated", user_id=str(user.id), old_session_id=str(s.id))
        return access_token, new_refresh_token, expires_in

    # -------------------------------------------------------------------------
    # 4. Logout (idempotent)
    # -------------------------------------------------------------------------

    async def logout(self, refresh_token: str) -> None:
        now = utc_now()
        token_hash = hash_token(refresh_token)
        # Locate the session BEFORE revoking it so we know whose perms cache
        # to drop.
        s = await self.repo.get_active_session_by_hash(token_hash)
        revoked = await self.repo.revoke_session_by_hash(token_hash, reason="logout", when=now)
        if revoked and s is not None and self.redis is not None:
            # Local import — RolesService pulls from roles domain, which
            # imports auth at module level. Lazy keeps the load DAG acyclic.
            from app.domains.roles.repository import RolesRepository
            from app.domains.roles.service import RolesService

            roles_service = RolesService(RolesRepository(self.repo.session), redis=self.redis)
            await roles_service.invalidate_user_perms_all_tenants(s.user_id)
        if revoked:
            logger.info("logout", token_hash_prefix=token_hash[:8])

    # -------------------------------------------------------------------------
    # 5. /me
    # -------------------------------------------------------------------------

    async def get_user_info(self, user_id: UUID) -> AppUser:
        user = await self.repo.get_user_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found")
        return user

    # -------------------------------------------------------------------------
    # internal
    # -------------------------------------------------------------------------

    async def _issue_tokens(
        self,
        *,
        user: AppUser,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[str, str, int]:
        now = utc_now()
        access_token = create_access_token(
            user.id,
            tenant_id=user.home_tenant_id,
            is_developer=user.is_developer,
            is_administrator=user.is_administrator,
        )
        refresh_token = generate_refresh_token()
        await self.repo.insert_session(
            user_id=user.id,
            refresh_token_hash=hash_token(refresh_token),
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=now + timedelta(days=settings.REFRESH_TOKEN_DAYS),
        )
        return (
            access_token,
            refresh_token,
            settings.ACCESS_TOKEN_MINUTES * 60,
        )
