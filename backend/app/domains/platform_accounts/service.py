"""Business rules for the Aurum Pharma platform team."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.errors import AuthenticationError, ConflictError, PermissionDeniedError
from app.core.security import hash_password, hash_token
from app.core.time import utc_now
from app.domains.platform_accounts.repository import (
    PlatformAccountsRepository,
    PlatformStaffAccountRecord,
)

ACCOUNTS_VIEW = "platform.accounts.view"
ACCOUNTS_MANAGE = "platform.accounts.manage"
INVITATION_LIFETIME = timedelta(hours=24)


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
