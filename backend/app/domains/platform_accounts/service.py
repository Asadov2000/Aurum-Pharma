"""Business rules for the Aurum Pharma platform team."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from uuid import UUID

import structlog
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.errors import (
    AurumError,
    AuthenticationError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.security import hash_password, hash_token
from app.core.time import utc_now
from app.domains.platform_accounts.repository import (
    PlatformAccountsRepository,
    PlatformStaffAccountRecord,
)

ACCOUNTS_VIEW = "platform.accounts.view"
ACCOUNTS_MANAGE = "platform.accounts.manage"
INVITATION_LIFETIME = timedelta(hours=24)
PlatformStaffAction = Literal["block", "unblock", "offboard"]
logger = structlog.get_logger("platform_accounts.service")


def _lifecycle_error(exc: DBAPIError) -> AurumError:
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "42501":
        return PermissionDeniedError("Platform account operation is not allowed")
    if sqlstate == "P0002":
        return NotFoundError("Platform staff account not found")
    if sqlstate in {"22023", "23502"}:
        return BusinessRuleError("Platform account request is invalid")
    if sqlstate in {"23505", "23514", "40001", "40P01", "55000"}:
        return ConflictError("Platform account changed; refresh and retry")
    logger.error("platform_account_database_guard_failed", sqlstate=sqlstate)
    return AurumError("Platform account database guard failed")


@dataclass(frozen=True)
class PlatformStaffInvitationResult:
    account: PlatformStaffAccountRecord
    activation_token: str | None


class PlatformAccountsService:
    def __init__(
        self,
        repo: PlatformAccountsRepository,
        *,
        expose_activation_token: bool = False,
    ) -> None:
        self.repo = repo
        self.expose_activation_token = expose_activation_token

    async def _require_capability(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        capability: str,
    ) -> None:
        if not await self.repo.actor_has_capability(
            actor_user_id=actor_user_id,
            actor_session_id=actor_session_id,
            capability=capability,
        ):
            raise PermissionDeniedError("Platform account capability required")

    async def list_accounts(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        query: str | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[PlatformStaffAccountRecord], int]:
        await self._require_capability(
            actor_user_id=actor_user_id,
            actor_session_id=actor_session_id,
            capability=ACCOUNTS_VIEW,
        )
        normalized_query = " ".join(query.split()) if query else None
        return await self.repo.list_accounts(
            query=normalized_query or None,
            status=status,
            limit=limit,
            offset=offset,
        )

    async def invite(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        email: str,
        full_name: str,
    ) -> PlatformStaffInvitationResult:
        await self._require_capability(
            actor_user_id=actor_user_id,
            actor_session_id=actor_session_id,
            capability=ACCOUNTS_MANAGE,
        )
        activation_token = secrets.token_urlsafe(32)
        try:
            account = await self.repo.create_invitation(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                email=email.lower(),
                full_name=full_name,
                token_hash=hash_token(activation_token),
                expires_at=utc_now() + INVITATION_LIFETIME,
            )
        except IntegrityError as exc:
            raise ConflictError("Account cannot be invited") from exc
        return PlatformStaffInvitationResult(
            account=account,
            activation_token=activation_token if self.expose_activation_token else None,
        )

    async def activate(self, *, token: str, password: str) -> None:
        token_hash = hash_token(token)
        if not await self.repo.invitation_is_usable(token_hash):
            raise AuthenticationError("Invitation is invalid or expired")
        password_hash = hash_password(password)
        activated_user_id = await self.repo.accept_invitation(
            token_hash=token_hash,
            password_hash=password_hash,
        )
        if activated_user_id is None:
            raise AuthenticationError("Invitation is invalid or expired")

    async def reinvite(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        user_id: UUID,
        version: int,
        operation_id: UUID,
        reason_code: str,
        reason: str,
    ) -> PlatformStaffInvitationResult:
        await self._require_capability(
            actor_user_id=actor_user_id,
            actor_session_id=actor_session_id,
            capability=ACCOUNTS_MANAGE,
        )
        activation_token = secrets.token_urlsafe(32)
        try:
            result = await self.repo.reinvite(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                user_id=user_id,
                version=version,
                operation_id=operation_id,
                reason_code=reason_code,
                reason=reason,
                token_hash=hash_token(activation_token),
                expires_at=utc_now() + INVITATION_LIFETIME,
            )
        except DBAPIError as exc:
            raise _lifecycle_error(exc) from exc
        if result is None:
            raise ConflictError("Platform account changed; refresh and retry")
        updated, applied = result
        return PlatformStaffInvitationResult(
            account=updated,
            activation_token=(
                activation_token if applied and self.expose_activation_token else None
            ),
        )

    async def change_status(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        user_id: UUID,
        version: int,
        operation_id: UUID,
        action: PlatformStaffAction,
        reason_code: str,
        reason: str,
    ) -> PlatformStaffAccountRecord:
        await self._require_capability(
            actor_user_id=actor_user_id,
            actor_session_id=actor_session_id,
            capability=ACCOUNTS_MANAGE,
        )
        if user_id == actor_user_id:
            raise PermissionDeniedError("Platform account cannot change its own lifecycle state")
        try:
            updated = await self.repo.change_status(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                user_id=user_id,
                version=version,
                operation_id=operation_id,
                action=action,
                reason_code=reason_code,
                reason=reason,
            )
        except DBAPIError as exc:
            raise _lifecycle_error(exc) from exc
        if updated is None:
            raise ConflictError("Platform account changed; refresh and retry")
        return updated
