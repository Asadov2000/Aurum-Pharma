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
from app.domains.auth.repository import AuthRepository, EmailCodeIssueStatus

if TYPE_CHECKING:
    from app.domains.auth.repository import AuthUserRecord

logger = structlog.get_logger("auth.service")
settings = get_settings()


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
    ) -> tuple[str, str, int]:
        """Returns (access_token, refresh_token, access_expires_in_seconds)."""
        email_lower = email.strip().lower()
        now = utc_now()

        if await self.repo.enforce_login_guard(email_lower=email_lower, ip_address=ip_address):
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
        if user is None or user.status not in ("invited", "active"):
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
        needs_password = bool(user.password_hash) or user.password_required
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

        access_token = create_access_token(
            user.id,
            tenant_id=user.home_tenant_id,
            is_developer=user.is_developer,
            is_administrator=user.is_administrator,
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
        return access_token, refresh_token, settings.ACCESS_TOKEN_MINUTES * 60

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
        token_hash = hash_token(refresh_token)
        s = await self.repo.get_active_session_by_hash(token_hash)
        if s is None:
            raise AuthenticationError("Invalid or expired refresh token")

        user = await self.repo.get_user_by_id(s.user_id, session_id=s.id)
        if user is None or user.status not in ("invited", "active"):
            await self.repo.revoke_session_by_hash(token_hash, reason="user_inactive")
            raise AuthenticationError("Invalid or expired refresh token")

        new_refresh_token = generate_refresh_token()
        rotated = await self.repo.rotate_session(
            old_token_hash=token_hash,
            new_token_hash=hash_token(new_refresh_token),
            user_agent=user_agent,
            ip_address=ip_address,
            expires_at=utc_now() + timedelta(days=settings.REFRESH_TOKEN_DAYS),
        )
        if rotated is None or rotated.user_id != user.id:
            raise AuthenticationError("Invalid or expired refresh token")

        access_token = create_access_token(
            user.id,
            tenant_id=user.home_tenant_id,
            is_developer=user.is_developer,
            is_administrator=user.is_administrator,
        )
        logger.info("refresh_rotated", user_id=str(user.id), old_session_id=str(s.id))
        return access_token, new_refresh_token, settings.ACCESS_TOKEN_MINUTES * 60

    # -------------------------------------------------------------------------
    # 4. Logout (idempotent)
    # -------------------------------------------------------------------------

    async def logout(self, refresh_token: str) -> None:
        token_hash = hash_token(refresh_token)
        user_id = await self.repo.revoke_session_by_hash(token_hash, reason="logout")
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
    # 5. /me
    # -------------------------------------------------------------------------

    async def get_user_info(self, user_id: UUID) -> AuthUserRecord:
        user = await self.repo.get_user_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found")
        return user
