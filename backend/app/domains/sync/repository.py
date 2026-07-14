"""Database access for the transactional sync outbox."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.sync.models import SyncOutboxEvent


class SyncOutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_operation_id(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
    ) -> SyncOutboxEvent | None:
        result = await self.session.execute(
            select(SyncOutboxEvent).where(
                SyncOutboxEvent.tenant_id == tenant_id,
                SyncOutboxEvent.operation_id == operation_id,
            )
        )
        return result.scalar_one_or_none()

    async def enqueue(
        self,
        *,
        event_id: UUID,
        tenant_id: UUID,
        branch_id: UUID,
        operation_id: UUID,
        aggregate_type: str,
        aggregate_id: UUID,
        event_type: str,
        schema_version: int,
        payload: dict[str, object],
        payload_hash: str,
    ) -> SyncOutboxEvent:
        result = await self.session.execute(
            insert(SyncOutboxEvent)
            .values(
                event_id=event_id,
                tenant_id=tenant_id,
                branch_id=branch_id,
                operation_id=operation_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_type=event_type,
                schema_version=schema_version,
                payload=payload,
                payload_hash=payload_hash,
            )
            .returning(SyncOutboxEvent)
        )
        return result.scalar_one()
