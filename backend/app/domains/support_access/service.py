"""Business rules for short-lived, explicitly scoped support access."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import structlog

from app.core.errors import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.core.time import utc_now
from app.domains.audit.repository import AuditRepository
from app.domains.audit.service import AuditService
from app.domains.support_access.repository import (
    SupportAccessRepository,
    SupportAccessSessionRecord,
    SupportCapabilityRecord,
)

logger = structlog.get_logger("support_access.service")

SUPPORT_ROLE_CAPABILITIES = (
    "branches.view",
    "roles.assign",
    "roles.create",
    "roles.update",
    "users.block",
    "users.view",
)
SUPPORT_WRITE_CAPABILITIES = frozenset(
    {"roles.assign", "roles.create", "roles.update", "users.block"}
)


class SupportAccessService:
    def __init__(self, repo: SupportAccessRepository) -> None:
        self.repo = repo
        self.audit = AuditService(AuditRepository(repo.session))

    async def list_capabilities(
        self,
        *,
        actor_is_developer: bool,
        actor_is_administrator: bool,
    ) -> list[SupportCapabilityRecord]:
        return await self.repo.list_capabilities(
            is_developer=actor_is_developer,
            is_administrator=actor_is_administrator,
            allowed_codes=SUPPORT_ROLE_CAPABILITIES,
        )

    async def start_session(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        actor_is_developer: bool,
        actor_is_administrator: bool,
        tenant_id: UUID,
        reason: str,
        duration_minutes: int,
        requested_capabilities: list[str],
    ) -> SupportAccessSessionRecord:
        actor = await self.repo.lock_actor(actor_user_id)
        if actor is None or actor[2] != "active":
            raise PermissionDeniedError("Active support account required")
        if actor[:2] != (actor_is_developer, actor_is_administrator):
            raise PermissionDeniedError("Support account claims are outdated")
        if not any(actor[:2]):
            raise PermissionDeniedError("Support privileges required")
        if not await self.repo.auth_session_is_active(
            actor_user_id=actor_user_id,
            actor_session_id=actor_session_id,
        ):
            raise PermissionDeniedError("Active authentication session required")

        tenant_status = await self.repo.tenant_status(tenant_id)
        if tenant_status is None:
            raise NotFoundError("Tenant not found")

        catalog = await self.list_capabilities(
            actor_is_developer=actor_is_developer,
            actor_is_administrator=actor_is_administrator,
        )
        allowed = {capability.code for capability in catalog}
        requested = list(dict.fromkeys(requested_capabilities))
        unavailable = sorted(set(requested) - allowed)
        if unavailable:
            raise PermissionDeniedError(
                "Support capabilities are outside the allowed scope",
                details={"permissions": unavailable},
            )
        if not requested:
            raise BusinessRuleError("At least one support capability is required")

        is_read_only = not bool(set(requested) & SUPPORT_WRITE_CAPABILITIES)
        if tenant_status == "archived" and not is_read_only:
            raise BusinessRuleError("Archived tenant support is read-only")

        now = utc_now()
        for replaced in await self.repo.revoke_active_for_actor(
            actor_user_id=actor_user_id,
            actor_session_id=actor_session_id,
            revoked_at=now,
        ):
            await self._audit_session(replaced, event="replaced")

        session = await self.repo.create_session(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            actor_session_id=actor_session_id,
            reason=reason,
            capabilities=requested,
            is_read_only=is_read_only,
            started_at=now,
            expires_at=now + timedelta(minutes=duration_minutes),
        )
        await self._audit_session(session, event="started")
        logger.info(
            "support_access_started",
            support_access_session_id=str(session.id),
            tenant_id=str(tenant_id),
            actor_user_id=str(actor_user_id),
            capabilities=len(session.capabilities),
        )
        return session

    async def list_active_sessions(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
    ) -> list[SupportAccessSessionRecord]:
        return await self.repo.list_active_for_actor(
            actor_user_id=actor_user_id,
            actor_session_id=actor_session_id,
            now=utc_now(),
        )

    async def revoke_session(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        session_id: UUID,
    ) -> None:
        session = await self.repo.revoke_session(
            session_id=session_id,
            actor_user_id=actor_user_id,
            actor_session_id=actor_session_id,
            revoked_at=utc_now(),
        )
        if session is None:
            existing = await self.repo.get_for_actor(
                session_id=session_id,
                actor_user_id=actor_user_id,
            )
            if existing is None:
                raise NotFoundError("Support access session not found")
            return
        await self._audit_session(session, event="revoked")
        logger.info(
            "support_access_revoked",
            support_access_session_id=str(session.id),
            tenant_id=str(session.tenant_id),
            actor_user_id=str(actor_user_id),
        )

    async def _audit_session(
        self,
        session: SupportAccessSessionRecord,
        *,
        event: str,
    ) -> None:
        await self.audit.log_impersonate(
            support_user_id=session.actor_user_id,
            tenant_id=session.tenant_id,
            metadata={
                "event": f"support_access_{event}",
                "support_access_session_id": str(session.id),
                "reason": session.reason,
                "capabilities": list(session.capabilities),
                "is_read_only": session.is_read_only,
                "started_at": session.started_at.isoformat(),
                "expires_at": session.expires_at.isoformat(),
                "revoked_at": (
                    session.revoked_at.isoformat() if session.revoked_at is not None else None
                ),
            },
        )
