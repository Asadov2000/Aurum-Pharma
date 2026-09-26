"""Transactional durable storage for explicitly provisioned local authentication."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.sync.models import LocalAuthGuard, LocalAuthSession
from app.domains.sync.offline_auth_lease_state import LocalOfflineLeaseSessionV1
from app.domains.sync.schemas import OfflineAuthScopeV0


class LocalAuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_guard(
        self, *, tenant_id: UUID, edge_node_id: UUID, user_id: UUID, credential_id: UUID
    ) -> LocalAuthGuard | None:
        result = await self.session.execute(
            select(LocalAuthGuard)
            .where(
                LocalAuthGuard.tenant_id == tenant_id,
                LocalAuthGuard.edge_node_id == edge_node_id,
                LocalAuthGuard.user_id == user_id,
                LocalAuthGuard.credential_id == credential_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def lock_workplace(
        self, *, tenant_id: UUID, branch_id: UUID, edge_node_id: UUID, register_id: UUID
    ) -> None:
        scope = ":".join(str(value) for value in (tenant_id, branch_id, edge_node_id, register_id))
        digest = hashlib.sha256(f"aurum-local-auth-workplace:{scope}".encode("ascii")).digest()
        lock_id = int.from_bytes(digest[:8], byteorder="big", signed=True)
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id}
        )

    async def replace_session(self, record: LocalOfflineLeaseSessionV1) -> None:
        validated = LocalOfflineLeaseSessionV1.model_validate(record)
        scope = validated.scope
        await self.session.execute(
            update(LocalAuthSession)
            .where(
                LocalAuthSession.tenant_id == scope.tenant_id,
                LocalAuthSession.branch_id == scope.branch_id,
                LocalAuthSession.edge_node_id == scope.edge_node_id,
                LocalAuthSession.register_id == scope.register_id,
                LocalAuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=validated.authenticated_at)
        )
        self.session.add(
            LocalAuthSession(
                id=validated.session_id,
                tenant_id=scope.tenant_id,
                branch_id=scope.branch_id,
                edge_node_id=scope.edge_node_id,
                register_id=scope.register_id,
                user_id=scope.user_id,
                credential_id=validated.local_credential.reference_id,
                payload=validated.model_dump(mode="json"),
            )
        )
        await self.session.flush()

    async def read_current_session(
        self, scope: OfflineAuthScopeV0
    ) -> LocalOfflineLeaseSessionV1 | None:
        row = await self.session.scalar(
            select(LocalAuthSession).where(
                LocalAuthSession.tenant_id == scope.tenant_id,
                LocalAuthSession.branch_id == scope.branch_id,
                LocalAuthSession.edge_node_id == scope.edge_node_id,
                LocalAuthSession.register_id == scope.register_id,
                LocalAuthSession.revoked_at.is_(None),
            )
        )
        if row is None:
            return None
        record = LocalOfflineLeaseSessionV1.model_validate_json(json.dumps(row.payload))
        if (
            record.session_id != row.id
            or record.scope != scope
            or record.local_credential.reference_id != row.credential_id
            or record.scope.user_id != row.user_id
        ):
            raise ValueError("stored local session scope is inconsistent")
        return record
