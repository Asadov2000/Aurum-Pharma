"""Preverify the active local Edge snapshot outside user request paths."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import AppSessionLocal
from app.core.errors import ServiceUnavailableError
from app.core.logging import get_logger
from app.domains.sync.credentials import parse_edge_credential
from app.domains.sync.models import SyncReadSnapshot
from app.domains.sync.read_model import LocalReadModel, invalidate_verified_source
from app.domains.sync.repository import SyncEdgeRepository

logger = get_logger("sync.readiness")
_POLL_SECONDS = 5


class EdgeSnapshotReadiness:
    def __init__(self) -> None:
        self._verified: tuple[UUID, UUID, UUID, UUID, str] | None = None
        self._credential_expires_at: datetime | None = None

    @property
    def is_ready(self) -> bool:
        return (
            self._verified is not None
            and self._credential_expires_at is not None
            and self._credential_expires_at > datetime.now(UTC)
        )

    def require_verified(self, snapshot: SyncReadSnapshot) -> None:
        key = (
            snapshot.tenant_id,
            snapshot.branch_id,
            snapshot.node_id,
            snapshot.snapshot_id,
            snapshot.signature,
        )
        if not self.is_ready or self._verified != key:
            raise ServiceUnavailableError("Local Edge snapshot verification is pending")

    async def poll_once(self) -> None:
        credential_secret = get_settings().EDGE_SYNC_CREDENTIAL
        if credential_secret is None:
            self._verified = None
            return
        credential = parse_edge_credential(credential_secret.get_secret_value())

        # This existing narrowly scoped database function authenticates the
        # credential and returns its tenant/branch/node without bypassing RLS in
        # Python. Ordinary snapshot reads below still use aurum_app with RLS.
        async with AppSessionLocal() as session, session.begin():
            result = await session.execute(
                text("SELECT * FROM public.authenticate_edge_node(:kid, :digest)"),
                {"kid": credential.kid, "digest": credential.digest},
            )
            row = result.mappings().one_or_none()
        if row is None:
            self._verified = None
            return
        tenant_id: UUID = row["tenant_id"]
        branch_id: UUID = row["branch_id"]
        node_id: UUID = row["node_id"]
        self._credential_expires_at = row["credential_expires_at"]

        async with AppSessionLocal() as session, session.begin():
            await session.execute(text("SET TRANSACTION READ ONLY"))
            for name, value in (
                ("app.tenant_id", tenant_id),
                ("app.branch_id", branch_id),
                ("app.edge_node_id", node_id),
            ):
                await session.execute(
                    text("SELECT set_config(:name, :value, true)"),
                    {"name": name, "value": str(value)},
                )
            repo = SyncEdgeRepository(session)
            stored = await repo.get_active_read_projection(
                tenant_id=tenant_id,
                branch_id=branch_id,
                credential_kid=credential.kid,
            )
            if stored is None:
                self._verified = None
                return
            snapshot = stored[0]
            key = (tenant_id, branch_id, node_id, snapshot.snapshot_id, snapshot.signature)
            if self._verified == key and self.is_ready:
                return
            self._verified = None
            invalidate_verified_source(snapshot)
            await LocalReadModel(repo).foundation(tenant_id=tenant_id, branch_id=branch_id)
            self._verified = key

    async def refresh(self) -> None:
        try:
            await self.poll_once()
        except Exception as exc:
            self._verified = None
            logger.warning("edge_snapshot_preverify_fail", error_type=type(exc).__name__)

    async def run(self) -> None:
        while True:
            await asyncio.sleep(_POLL_SECONDS)
            await self.refresh()


edge_snapshot_readiness = EdgeSnapshotReadiness()
