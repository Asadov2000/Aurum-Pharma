"""Durable inactive delivery storage using ordinary tenant-scoped access.

The inbox lock serializes deliveries only. Enrollment is a snapshot of current
activation, epoch and stream records, not protected enrollment or authorization.
Private node lifecycle and TPM identity are not readable by this runtime role;
node revocation and concurrent writer retirement cannot make a pending row safe
to activate. Future protected acceptance must independently recheck node lifecycle,
device binding and writer ownership under trusted serialization before granting anything.
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.sync.models import (
    OfflinePolicyInbox,
    SyncStream,
    SyncWriterActivation,
    SyncWriterEpoch,
)
from app.domains.sync.schemas import OfflinePolicyDeliveryScopeV1, SignedOfflinePolicyDeliveryV1


def _target(scope: OfflinePolicyDeliveryScopeV1) -> tuple[ColumnElement[bool], ...]:
    return (
        OfflinePolicyInbox.tenant_id == scope.tenant_id,
        OfflinePolicyInbox.activation_id == scope.activation_id,
        OfflinePolicyInbox.branch_id == scope.branch_id,
        OfflinePolicyInbox.edge_node_id == scope.edge_node_id,
        OfflinePolicyInbox.register_id == scope.register_id,
        OfflinePolicyInbox.writer_epoch == scope.writer_epoch,
        OfflinePolicyInbox.capability == scope.capability,
    )


class OfflinePolicyInboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_target(self, scope: OfflinePolicyDeliveryScopeV1) -> None:
        target = (
            f"{scope.tenant_id}:{scope.activation_id}:{scope.edge_node_id}:{scope.writer_epoch}"
        )
        digest = hashlib.sha256(f"aurum-offline-policy-inbox:{target}".encode("ascii")).digest()
        lock_id = int.from_bytes(digest[:8], byteorder="big", signed=True)
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id}
        )

    async def assert_enrollment(self, scope: OfflinePolicyDeliveryScopeV1) -> None:
        """Check existing writer records without creating or activating enrollment."""
        activation_id = await self.session.scalar(
            select(SyncWriterActivation.activation_id)
            .join(
                SyncWriterEpoch,
                (SyncWriterEpoch.activation_id == SyncWriterActivation.activation_id)
                & (SyncWriterEpoch.tenant_id == SyncWriterActivation.tenant_id)
                & (SyncWriterEpoch.branch_id == SyncWriterActivation.branch_id)
                & (SyncWriterEpoch.writer_epoch == SyncWriterActivation.writer_epoch),
            )
            .join(
                SyncStream,
                (SyncStream.tenant_id == SyncWriterEpoch.tenant_id)
                & (SyncStream.branch_id == SyncWriterEpoch.branch_id)
                & (SyncStream.writer_epoch == SyncWriterEpoch.writer_epoch)
                & (SyncStream.writer_node_id == SyncWriterEpoch.writer_node_id),
            )
            .where(
                SyncWriterActivation.activation_id == scope.activation_id,
                SyncWriterActivation.tenant_id == scope.tenant_id,
                SyncWriterActivation.branch_id == scope.branch_id,
                SyncWriterActivation.writer_epoch == scope.writer_epoch,
                SyncWriterActivation.writer_node_id == scope.edge_node_id,
                SyncWriterActivation.allowed_register_id == scope.register_id,
                SyncWriterActivation.capability == scope.capability,
                SyncWriterActivation.state == "activated",
                SyncWriterEpoch.writer_node_id == scope.edge_node_id,
                SyncWriterEpoch.allowed_register_id == scope.register_id,
                SyncWriterEpoch.capability == scope.capability,
                SyncWriterEpoch.state == "active",
            )
        )
        if activation_id is None:
            raise ValueError("Offline policy target has no current writer enrollment")

    async def get_by_id(
        self, scope: OfflinePolicyDeliveryScopeV1, delivery_id: UUID
    ) -> OfflinePolicyInbox | None:
        result = await self.session.execute(
            select(OfflinePolicyInbox)
            .where(*_target(scope), OfflinePolicyInbox.id == delivery_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_latest(self, scope: OfflinePolicyDeliveryScopeV1) -> OfflinePolicyInbox | None:
        result = await self.session.execute(
            select(OfflinePolicyInbox)
            .where(*_target(scope))
            .order_by(OfflinePolicyInbox.delivery_sequence.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def insert(self, envelope: SignedOfflinePolicyDeliveryV1) -> OfflinePolicyInbox:
        claims = envelope.claims
        scope = claims.scope
        row = OfflinePolicyInbox(
            id=claims.delivery_id,
            tenant_id=scope.tenant_id,
            activation_id=scope.activation_id,
            branch_id=scope.branch_id,
            edge_node_id=scope.edge_node_id,
            register_id=scope.register_id,
            writer_epoch=scope.writer_epoch,
            capability=scope.capability,
            delivery_sequence=claims.delivery_sequence,
            tenant_access_revision=claims.policy.version,
            issued_at=claims.issued_at,
            payload=envelope.model_dump(mode="json"),
            claims_hash=envelope.claims_hash,
            status="pending_protected_acceptance",
        )
        self.session.add(row)
        await self.session.flush()
        return row
