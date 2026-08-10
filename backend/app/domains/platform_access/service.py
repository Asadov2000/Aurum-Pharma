"""Business rules for protected platform access grants."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import structlog

from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.time import utc_now
from app.domains.platform_access.repository import (
    PlatformAccessGrantRecord,
    PlatformAccessRepository,
    PlatformActorRecord,
    PlatformCapabilityRecord,
    PlatformTargetRecord,
)

logger = structlog.get_logger("platform_access.service")

APPROVAL_WINDOW = timedelta(minutes=15)
ACCESS_VIEW = "platform.access.view"
ACCESS_MANAGE = "platform.access.manage"
MANDATORY_DEVELOPER_CAPABILITIES = frozenset({ACCESS_VIEW, ACCESS_MANAGE})


class PlatformAccessService:
    def __init__(self, repo: PlatformAccessRepository) -> None:
        self.repo = repo

    async def _require_actor(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        actor_is_developer: bool,
        required_capability: str,
    ) -> PlatformActorRecord:
        actor = await self.repo.lock_actor(actor_user_id)
        if (
            actor is None
            or actor.status != "active"
            or not actor_is_developer
            or not actor.is_developer
            or not actor.has_active_developer_grant
            or required_capability not in actor.capabilities
        ):
            raise PermissionDeniedError("Active Developer access required")
        if not await self.repo.auth_session_is_active(
            actor_user_id=actor_user_id,
            actor_session_id=actor_session_id,
        ):
            raise PermissionDeniedError("Active authentication session required")
        return actor

    async def _require_capability_envelope(
        self,
        *,
        actor: PlatformActorRecord,
        actor_user_id: UUID,
        access_kind: str,
        capabilities: tuple[str, ...],
    ) -> None:
        catalog = await self.repo.list_grantable_capabilities(
            actor_user_id=actor_user_id,
            access_kind=access_kind,
        )
        allowed = {capability.code for capability in catalog}
        requested = set(capabilities)
        if not requested or not requested.issubset(allowed):
            raise PermissionDeniedError("Capabilities are outside the delegation envelope")
        if not requested.issubset(actor.capabilities):
            raise PermissionDeniedError("Capabilities exceed the Developer grant")
        if access_kind == "developer" and not MANDATORY_DEVELOPER_CAPABILITIES.issubset(requested):
            raise BusinessRuleError(
                "Developer grant requires platform access governance",
                details={"required_capabilities": sorted(MANDATORY_DEVELOPER_CAPABILITIES)},
            )

    @staticmethod
    def _require_eligible_target(target: PlatformTargetRecord | None) -> None:
        if (
            target is None
            or target.status != "active"
            or target.home_tenant_id is not None
            or target.has_membership
            or target.is_developer
            or target.is_administrator
        ):
            raise BusinessRuleError("Target account is not eligible for platform access")

    async def request_grant(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        actor_is_developer: bool,
        user_id: UUID,
        access_kind: str,
        reason_code: str,
        reason: str,
        capabilities: tuple[str, ...],
    ) -> PlatformAccessGrantRecord:
        await self.repo.acquire_control_plane_lock()
        actor = await self._require_actor(
            actor_user_id=actor_user_id,
            actor_session_id=actor_session_id,
            actor_is_developer=actor_is_developer,
            required_capability=ACCESS_MANAGE,
        )
        if user_id == actor_user_id:
            raise PermissionDeniedError("Platform access cannot be self-assigned")

        target = await self.repo.lock_target(user_id)
        self._require_eligible_target(target)
        await self._require_capability_envelope(
            actor=actor,
            actor_user_id=actor_user_id,
            access_kind=access_kind,
            capabilities=capabilities,
        )
        await self.repo.expire_pending_for_target(
            user_id=user_id,
            actor_user_id=actor_user_id,
        )
        if await self.repo.has_current_grant(user_id):
            raise ConflictError("Target already has pending or active platform access")

        requires_approval = await self.repo.has_other_active_developer(actor_user_id)
        grant = await self.repo.create_grant(
            user_id=user_id,
            access_kind=access_kind,
            actor_user_id=actor_user_id,
            reason_code=reason_code,
            reason=reason,
            requires_approval=requires_approval,
            approval_expires_at=utc_now() + APPROVAL_WINDOW if requires_approval else None,
            capabilities=capabilities,
        )
        logger.info(
            "platform_access_requested",
            grant_id=str(grant.id),
            target_user_id=str(user_id),
            actor_user_id=str(actor_user_id),
            access_kind=access_kind,
            status=grant.status,
        )
        return grant

    async def approve_grant(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        actor_is_developer: bool,
        grant_id: UUID,
        version: int,
        reason_code: str,
        reason: str,
    ) -> PlatformAccessGrantRecord:
        await self.repo.acquire_control_plane_lock()
        actor = await self._require_actor(
            actor_user_id=actor_user_id,
            actor_session_id=actor_session_id,
            actor_is_developer=actor_is_developer,
            required_capability=ACCESS_MANAGE,
        )
        grant = await self.repo.lock_grant(grant_id)
        if grant is None:
            raise NotFoundError("Platform access grant not found")
        if grant.user_id == actor_user_id:
            raise PermissionDeniedError("Platform access cannot be self-approved")
        if grant.requested_by == actor_user_id:
            raise PermissionDeniedError("A different Developer must approve this request")
        if grant.status != "pending":
            raise ConflictError("Platform access request is no longer pending")
        if grant.version != version:
            raise ConflictError(
                "Platform access request changed",
                details={"current_version": grant.version},
            )
        await self._require_capability_envelope(
            actor=actor,
            actor_user_id=actor_user_id,
            access_kind=grant.access_kind,
            capabilities=grant.capabilities,
        )

        target = await self.repo.lock_target(grant.user_id)
        self._require_eligible_target(target)

        approved = await self.repo.approve_grant(
            grant_id=grant_id,
            actor_user_id=actor_user_id,
            version=version,
            reason_code=reason_code,
            reason=reason,
        )
        if approved is None:
            expired = await self.repo.expire_grant(
                grant_id=grant_id,
                actor_user_id=actor_user_id,
                version=version,
            )
            if expired is None:
                raise ConflictError("Platform access request changed")
            logger.info(
                "platform_access_expired",
                grant_id=str(expired.id),
                target_user_id=str(expired.user_id),
                actor_user_id=str(actor_user_id),
                access_kind=expired.access_kind,
            )
            return expired

        logger.info(
            "platform_access_approved",
            grant_id=str(approved.id),
            target_user_id=str(approved.user_id),
            actor_user_id=str(actor_user_id),
            access_kind=approved.access_kind,
        )
        return approved

    async def revoke_grant(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        actor_is_developer: bool,
        grant_id: UUID,
        version: int,
        reason_code: str,
        reason: str,
    ) -> PlatformAccessGrantRecord:
        await self.repo.acquire_control_plane_lock()
        await self._require_actor(
            actor_user_id=actor_user_id,
            actor_session_id=actor_session_id,
            actor_is_developer=actor_is_developer,
            required_capability=ACCESS_MANAGE,
        )
        grant = await self.repo.lock_grant(grant_id)
        if grant is None:
            raise NotFoundError("Platform access grant not found")
        if grant.user_id == actor_user_id:
            raise PermissionDeniedError("Platform access cannot be self-revoked")
        if grant.status not in {"pending", "active"}:
            raise ConflictError("Platform access grant is no longer revocable")
        if grant.version != version:
            raise ConflictError(
                "Platform access grant changed",
                details={"current_version": grant.version},
            )
        if (
            grant.status == "active"
            and grant.access_kind == "developer"
            and not await self.repo.has_other_active_developer(grant.user_id)
        ):
            raise BusinessRuleError("The last active Developer cannot be revoked")

        revoked = await self.repo.revoke_grant(
            grant_id=grant_id,
            actor_user_id=actor_user_id,
            version=version,
            reason_code=reason_code,
            reason=reason,
        )
        if revoked is None:
            raise ConflictError("Platform access grant changed")
        logger.info(
            "platform_access_revoked",
            grant_id=str(revoked.id),
            target_user_id=str(revoked.user_id),
            actor_user_id=str(actor_user_id),
            access_kind=revoked.access_kind,
        )
        return revoked

    async def list_grants(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        actor_is_developer: bool,
        status: str | None,
        user_id: UUID | None,
        limit: int,
    ) -> list[PlatformAccessGrantRecord]:
        await self._require_actor(
            actor_user_id=actor_user_id,
            actor_session_id=actor_session_id,
            actor_is_developer=actor_is_developer,
            required_capability=ACCESS_VIEW,
        )
        return await self.repo.list_grants(
            status=status,
            user_id=user_id,
            limit=limit,
        )

    async def list_capabilities(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        actor_is_developer: bool,
        access_kind: str,
    ) -> list[PlatformCapabilityRecord]:
        await self._require_actor(
            actor_user_id=actor_user_id,
            actor_session_id=actor_session_id,
            actor_is_developer=actor_is_developer,
            required_capability=ACCESS_VIEW,
        )
        return await self.repo.list_grantable_capabilities(
            actor_user_id=actor_user_id,
            access_kind=access_kind,
        )
