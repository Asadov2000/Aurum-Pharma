"""Database access for Cloud outbox, Edge inbox, and shadow checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.sync.models import (
    SyncCursor,
    SyncInboxEvent,
    SyncNode,
    SyncOutboxEvent,
    SyncSaleProjection,
    SyncShadowReport,
    SyncStream,
    SyncWriterActivation,
    SyncWriterEpoch,
    SyncWriterReadiness,
)
from app.domains.sync.schemas import SyncEventEnvelope


@dataclass(frozen=True, slots=True)
class ReservedStreamPosition:
    stream_id: UUID
    origin_node_id: UUID
    writer_epoch: int
    sequence: int
    previous_checksum: str
    previous_projection_checksum: str


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

    async def reserve_position(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
    ) -> ReservedStreamPosition:
        await self.session.execute(
            text("SELECT set_config('app.branch_id', :branch_id, true)"),
            {"branch_id": str(branch_id)},
        )
        result = await self.session.execute(
            text("SELECT * FROM public.reserve_sync_event_position(:tenant_id, :branch_id)"),
            {"tenant_id": tenant_id, "branch_id": branch_id},
        )
        row = result.mappings().one()
        return ReservedStreamPosition(
            stream_id=row["stream_id"],
            origin_node_id=row["origin_node_id"],
            writer_epoch=int(row["writer_epoch"]),
            sequence=int(row["sequence"]),
            previous_checksum=str(row["previous_checksum"]),
            previous_projection_checksum=str(row["previous_projection_checksum"]),
        )

    async def enqueue(
        self,
        *,
        event_id: UUID,
        tenant_id: UUID,
        branch_id: UUID,
        origin_node_id: UUID,
        writer_epoch: int,
        sequence: int,
        operation_id: UUID,
        aggregate_type: str,
        aggregate_id: UUID,
        event_type: str,
        schema_version: int,
        occurred_at: datetime,
        payload: dict[str, object],
        payload_hash: str,
        stream_checksum: str,
        projection_hash: str,
        projection_checksum: str,
    ) -> SyncOutboxEvent:
        result = await self.session.execute(
            insert(SyncOutboxEvent)
            .values(
                event_id=event_id,
                tenant_id=tenant_id,
                branch_id=branch_id,
                origin_node_id=origin_node_id,
                writer_epoch=writer_epoch,
                sequence=sequence,
                operation_id=operation_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_type=event_type,
                schema_version=schema_version,
                occurred_at=occurred_at,
                payload=payload,
                payload_hash=payload_hash,
                stream_checksum=stream_checksum,
                projection_hash=projection_hash,
                projection_checksum=projection_checksum,
            )
            .returning(SyncOutboxEvent)
        )
        return result.scalar_one()

    async def finalize_position(
        self,
        *,
        stream_id: UUID,
        sequence: int,
        stream_checksum: str,
        projection_checksum: str,
    ) -> None:
        await self.session.execute(
            text(
                "SELECT public.finalize_sync_event_position("
                ":stream_id, :sequence, :stream_checksum, :projection_checksum)"
            ),
            {
                "stream_id": stream_id,
                "sequence": sequence,
                "stream_checksum": stream_checksum,
                "projection_checksum": projection_checksum,
            },
        )


class SyncCloudRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def branch_exists(self, *, tenant_id: UUID, branch_id: UUID) -> bool:
        result = await self.session.execute(
            text(
                "SELECT EXISTS(SELECT 1 FROM public.branch "
                "WHERE id = :branch_id AND tenant_id = :tenant_id)"
            ),
            {"tenant_id": tenant_id, "branch_id": branch_id},
        )
        return bool(result.scalar_one())

    async def ensure_stream(self, *, tenant_id: UUID, branch_id: UUID) -> SyncStream:
        await self.session.execute(
            text("""
                INSERT INTO public.sync_node (
                  tenant_id, branch_id, node_kind, mode, status, display_name
                ) VALUES (
                  :tenant_id, :branch_id, 'cloud', 'cloud_writer', 'active', 'Cloud writer'
                )
                ON CONFLICT (tenant_id, branch_id) WHERE node_kind = 'cloud'
                DO NOTHING
                """),
            {"tenant_id": tenant_id, "branch_id": branch_id},
        )
        await self.session.execute(
            text("""
                INSERT INTO public.sync_stream (
                  tenant_id, branch_id, writer_node_id, writer_epoch, last_sequence,
                  current_checksum, current_projection_checksum
                )
                SELECT
                  :tenant_id, :branch_id, sync_node.id, 1, 0, repeat('0', 64), repeat('0', 64)
                FROM public.sync_node AS sync_node
                WHERE sync_node.tenant_id = :tenant_id
                  AND sync_node.branch_id = :branch_id
                  AND sync_node.node_kind = 'cloud'
                ON CONFLICT (tenant_id, branch_id) DO NOTHING
                """),
            {"tenant_id": tenant_id, "branch_id": branch_id},
        )
        stream = await self.get_stream(tenant_id=tenant_id, branch_id=branch_id)
        if stream is None:
            raise RuntimeError("Sync stream was not created")
        return stream

    async def get_stream(self, *, tenant_id: UUID, branch_id: UUID) -> SyncStream | None:
        result = await self.session.execute(
            select(SyncStream).where(
                SyncStream.tenant_id == tenant_id,
                SyncStream.branch_id == branch_id,
            )
        )
        return result.scalar_one_or_none()

    async def create_edge_node(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        display_name: str,
        credential_kid: UUID,
        credential_hash: str,
        credential_issued_at: datetime,
        credential_expires_at: datetime,
        shadow_start_origin_node_id: UUID,
        shadow_start_writer_epoch: int,
        shadow_start_sequence: int,
        shadow_start_checksum: str,
        shadow_start_projection_checksum: str,
    ) -> SyncNode:
        result = await self.session.execute(
            insert(SyncNode)
            .values(
                tenant_id=tenant_id,
                branch_id=branch_id,
                node_kind="edge",
                mode="shadow_readonly",
                status="active",
                display_name=display_name,
                credential_kid=credential_kid,
                credential_hash=credential_hash,
                credential_issued_at=credential_issued_at,
                credential_expires_at=credential_expires_at,
                shadow_start_origin_node_id=shadow_start_origin_node_id,
                shadow_start_writer_epoch=shadow_start_writer_epoch,
                shadow_start_sequence=shadow_start_sequence,
                shadow_start_checksum=shadow_start_checksum,
                shadow_start_projection_checksum=shadow_start_projection_checksum,
            )
            .returning(SyncNode)
        )
        return result.scalar_one()

    async def list_edge_nodes(self, *, tenant_id: UUID | None = None) -> list[SyncNode]:
        stmt = select(SyncNode).where(SyncNode.node_kind == "edge")
        if tenant_id is not None:
            stmt = stmt.where(SyncNode.tenant_id == tenant_id)
        result = await self.session.execute(stmt.order_by(SyncNode.created_at, SyncNode.id))
        return list(result.scalars().all())

    async def get_edge_node(self, node_id: UUID) -> SyncNode | None:
        result = await self.session.execute(
            select(SyncNode).where(SyncNode.id == node_id, SyncNode.node_kind == "edge")
        )
        return result.scalar_one_or_none()

    async def rotate_edge_credential(
        self,
        *,
        node_id: UUID,
        credential_kid: UUID,
        credential_hash: str,
        credential_issued_at: datetime,
        credential_expires_at: datetime,
    ) -> SyncNode | None:
        result = await self.session.execute(
            update(SyncNode)
            .where(
                SyncNode.id == node_id,
                SyncNode.node_kind == "edge",
                SyncNode.status == "active",
            )
            .values(
                credential_kid=credential_kid,
                credential_hash=credential_hash,
                credential_issued_at=credential_issued_at,
                credential_expires_at=credential_expires_at,
            )
            .returning(SyncNode)
        )
        return result.scalar_one_or_none()

    async def revoke_edge_node(self, node_id: UUID) -> SyncNode | None:
        node = await self.session.scalar(
            select(SyncNode)
            .where(SyncNode.id == node_id, SyncNode.node_kind == "edge")
            .with_for_update()
        )
        if node is None:
            return None

        active_epoch = await self.session.scalar(
            select(SyncWriterEpoch.activation_id).where(
                SyncWriterEpoch.writer_node_id == node_id,
                SyncWriterEpoch.state == "active",
            )
        )
        pending_activation = await self.session.scalar(
            select(SyncWriterActivation.activation_id).where(
                SyncWriterActivation.writer_node_id == node_id,
                SyncWriterActivation.state.in_(("prepared", "ready")),
            )
        )
        if active_epoch is not None or pending_activation is not None:
            return None

        node.status = "revoked"
        await self.session.flush()
        return node

    async def get_writer_activation(self, activation_id: UUID) -> SyncWriterActivation | None:
        result = await self.session.execute(
            select(SyncWriterActivation)
            .where(SyncWriterActivation.activation_id == activation_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_writer_epoch(self, activation_id: UUID) -> SyncWriterEpoch | None:
        result = await self.session.execute(
            select(SyncWriterEpoch)
            .where(SyncWriterEpoch.activation_id == activation_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_writer_readiness(self, activation_id: UUID) -> SyncWriterReadiness | None:
        result = await self.session.execute(
            select(SyncWriterReadiness)
            .where(SyncWriterReadiness.activation_id == activation_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def prepare_writer_handover(
        self,
        *,
        activation_id: UUID,
        tenant_id: UUID,
        branch_id: UUID,
        edge_node_id: UUID,
        register_id: UUID,
        expected_writer_epoch: int,
        expected_sequence: int,
        expected_source_checksum: str,
        expected_projection_checksum: str,
        bootstrap_snapshot_hash: str,
        request_hash: str,
    ) -> SyncWriterActivation:
        result = await self.session.execute(
            text(
                "SELECT public.prepare_edge_writer_handover("
                ":activation_id, :tenant_id, :branch_id, :edge_node_id, :register_id, "
                ":expected_writer_epoch, :expected_sequence, :expected_source_checksum, "
                ":expected_projection_checksum, :bootstrap_snapshot_hash, :request_hash)"
            ),
            {
                "activation_id": activation_id,
                "tenant_id": tenant_id,
                "branch_id": branch_id,
                "edge_node_id": edge_node_id,
                "register_id": register_id,
                "expected_writer_epoch": expected_writer_epoch,
                "expected_sequence": expected_sequence,
                "expected_source_checksum": expected_source_checksum,
                "expected_projection_checksum": expected_projection_checksum,
                "bootstrap_snapshot_hash": bootstrap_snapshot_hash,
                "request_hash": request_hash,
            },
        )
        stored_activation_id = result.scalar_one()
        activation = await self.get_writer_activation(stored_activation_id)
        if activation is None:
            raise RuntimeError("Prepared writer activation was not persisted")
        return activation

    async def record_writer_readiness(
        self,
        *,
        activation_id: UUID,
        writer_epoch: int,
        previous_sequence: int,
        previous_source_checksum: str,
        previous_projection_checksum: str,
        bootstrap_snapshot_hash: str,
        activation_manifest_hash: str,
        receipt_baseline_seq: int,
        request_hash: str,
    ) -> SyncWriterReadiness:
        result = await self.session.execute(
            text(
                "SELECT public.record_edge_writer_readiness("
                ":activation_id, :writer_epoch, :previous_sequence, "
                ":previous_source_checksum, :previous_projection_checksum, "
                ":bootstrap_snapshot_hash, :activation_manifest_hash, "
                ":receipt_baseline_seq, :request_hash)"
            ),
            {
                "activation_id": activation_id,
                "writer_epoch": writer_epoch,
                "previous_sequence": previous_sequence,
                "previous_source_checksum": previous_source_checksum,
                "previous_projection_checksum": previous_projection_checksum,
                "bootstrap_snapshot_hash": bootstrap_snapshot_hash,
                "activation_manifest_hash": activation_manifest_hash,
                "receipt_baseline_seq": receipt_baseline_seq,
                "request_hash": request_hash,
            },
        )
        stored_activation_id = result.scalar_one()
        readiness = await self.get_writer_readiness(stored_activation_id)
        if readiness is None:
            raise RuntimeError("Writer readiness was not persisted")
        return readiness

    async def activate_writer_handover(
        self,
        *,
        activation_id: UUID,
        tenant_id: UUID,
        branch_id: UUID,
        activation_manifest_hash: str,
    ) -> SyncWriterEpoch:
        await self.session.execute(
            text("SELECT set_config('app.edge_writer_activation_enabled', 'true', true)")
        )
        result = await self.session.execute(
            text(
                "SELECT public.activate_edge_writer_handover("
                ":activation_id, :tenant_id, :branch_id, :activation_manifest_hash)"
            ),
            {
                "activation_id": activation_id,
                "tenant_id": tenant_id,
                "branch_id": branch_id,
                "activation_manifest_hash": activation_manifest_hash,
            },
        )
        stored_activation_id = result.scalar_one()
        epoch = await self.get_writer_epoch(stored_activation_id)
        if epoch is None:
            raise RuntimeError("Activated writer epoch was not persisted")
        return epoch

    async def cancel_writer_handover(
        self,
        *,
        activation_id: UUID,
        tenant_id: UUID,
        branch_id: UUID,
        activation_manifest_hash: str,
    ) -> SyncWriterActivation:
        result = await self.session.execute(
            text(
                "SELECT public.cancel_edge_writer_handover("
                ":activation_id, :tenant_id, :branch_id, :activation_manifest_hash)"
            ),
            {
                "activation_id": activation_id,
                "tenant_id": tenant_id,
                "branch_id": branch_id,
                "activation_manifest_hash": activation_manifest_hash,
            },
        )
        stored_activation_id = result.scalar_one()
        activation = await self.get_writer_activation(stored_activation_id)
        if activation is None:
            raise RuntimeError("Cancelled writer activation was not persisted")
        return activation

    async def list_events(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        origin_node_id: UUID,
        writer_epoch: int,
        after_sequence: int,
        limit: int,
        through_sequence: int | None = None,
    ) -> list[SyncOutboxEvent]:
        stmt = select(SyncOutboxEvent).where(
            SyncOutboxEvent.tenant_id == tenant_id,
            SyncOutboxEvent.branch_id == branch_id,
            SyncOutboxEvent.origin_node_id == origin_node_id,
            SyncOutboxEvent.writer_epoch == writer_epoch,
            SyncOutboxEvent.sequence > after_sequence,
            SyncOutboxEvent.stream_checksum.is_not(None),
            SyncOutboxEvent.projection_hash.is_not(None),
            SyncOutboxEvent.projection_checksum.is_not(None),
        )
        if through_sequence is not None:
            stmt = stmt.where(SyncOutboxEvent.sequence <= through_sequence)
        result = await self.session.execute(stmt.order_by(SyncOutboxEvent.sequence).limit(limit))
        return list(result.scalars().all())

    async def get_event_at_sequence(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        origin_node_id: UUID,
        writer_epoch: int,
        sequence: int,
    ) -> SyncOutboxEvent | None:
        result = await self.session.execute(
            select(SyncOutboxEvent).where(
                SyncOutboxEvent.tenant_id == tenant_id,
                SyncOutboxEvent.branch_id == branch_id,
                SyncOutboxEvent.origin_node_id == origin_node_id,
                SyncOutboxEvent.writer_epoch == writer_epoch,
                SyncOutboxEvent.sequence == sequence,
            )
        )
        return result.scalar_one_or_none()

    async def get_shadow_report(self, report_id: UUID) -> SyncShadowReport | None:
        return await self.session.get(SyncShadowReport, report_id)

    async def insert_shadow_report(
        self,
        *,
        report_id: UUID,
        tenant_id: UUID,
        branch_id: UUID,
        edge_node_id: UUID,
        origin_node_id: UUID,
        writer_epoch: int,
        last_sequence: int,
        source_checksum: str,
        expected_source_checksum: str,
        source_verified: bool,
        projection_checksum: str,
        expected_checksum: str,
        request_hash: str,
        status: str,
    ) -> SyncShadowReport:
        result = await self.session.execute(
            insert(SyncShadowReport)
            .values(
                report_id=report_id,
                tenant_id=tenant_id,
                branch_id=branch_id,
                edge_node_id=edge_node_id,
                origin_node_id=origin_node_id,
                writer_epoch=writer_epoch,
                last_sequence=last_sequence,
                source_checksum=source_checksum,
                expected_source_checksum=expected_source_checksum,
                source_verified=source_verified,
                projection_checksum=projection_checksum,
                expected_checksum=expected_checksum,
                request_hash=request_hash,
                status=status,
            )
            .returning(SyncShadowReport)
        )
        return result.scalar_one()


class SyncEdgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_cursor(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        origin_node_id: UUID,
        writer_epoch: int,
        for_update: bool = False,
    ) -> SyncCursor | None:
        stmt = select(SyncCursor).where(
            SyncCursor.tenant_id == tenant_id,
            SyncCursor.branch_id == branch_id,
            SyncCursor.origin_node_id == origin_node_id,
            SyncCursor.writer_epoch == writer_epoch,
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def insert_cursor(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        origin_node_id: UUID,
        writer_epoch: int,
        start_sequence: int,
        start_source_checksum: str,
        start_projection_checksum: str,
    ) -> SyncCursor:
        result = await self.session.execute(
            insert(SyncCursor)
            .values(
                tenant_id=tenant_id,
                branch_id=branch_id,
                origin_node_id=origin_node_id,
                writer_epoch=writer_epoch,
                start_sequence=start_sequence,
                start_source_checksum=start_source_checksum,
                start_projection_checksum=start_projection_checksum,
                last_sequence=start_sequence,
                source_checksum=start_source_checksum,
                projection_checksum=start_projection_checksum,
                status="synced",
            )
            .returning(SyncCursor)
        )
        return result.scalar_one()

    async def update_cursor(
        self,
        cursor: SyncCursor,
        *,
        last_sequence: int,
        last_event_id: UUID | None,
        source_checksum: str,
        projection_checksum: str,
        status: str,
    ) -> SyncCursor:
        cursor.last_sequence = last_sequence
        cursor.last_event_id = last_event_id
        cursor.source_checksum = source_checksum
        cursor.projection_checksum = projection_checksum
        cursor.status = status
        await self.session.flush()
        return cursor

    async def get_inbox_event(self, event_id: UUID) -> SyncInboxEvent | None:
        return await self.session.get(SyncInboxEvent, event_id)

    async def insert_inbox(
        self,
        envelope: SyncEventEnvelope,
        *,
        status: str,
        reason_code: str | None = None,
        applied_at: datetime | None = None,
    ) -> SyncInboxEvent:
        result = await self.session.execute(
            insert(SyncInboxEvent)
            .values(
                **envelope.model_dump(),
                status=status,
                reason_code=reason_code,
                applied_at=applied_at,
            )
            .returning(SyncInboxEvent)
        )
        return result.scalar_one()

    async def mark_inbox(
        self,
        event: SyncInboxEvent,
        *,
        status: str,
        reason_code: str | None,
        applied_at: datetime | None,
    ) -> SyncInboxEvent:
        event.status = status
        event.reason_code = reason_code
        event.applied_at = applied_at
        await self.session.flush()
        return event

    async def get_sale_projection(self, sale_id: UUID) -> SyncSaleProjection | None:
        return await self.session.get(SyncSaleProjection, sale_id)

    async def insert_sale_projection(
        self,
        *,
        sale_id: UUID,
        tenant_id: UUID,
        branch_id: UUID,
        origin_node_id: UUID,
        writer_epoch: int,
        sequence: int,
        source_event_id: UUID,
        operation_id: UUID,
        register_id: UUID,
        shift_id: UUID,
        cashier_user_id: UUID,
        receipt_number: str,
        receipt_seq: int,
        sale_created_at: datetime,
        completed_at: datetime,
        total_amount: Decimal,
        currency: str,
        is_test: bool,
        items: list[dict[str, object]],
        payments: list[dict[str, object]],
        source_payload_hash: str,
        projection_hash: str,
    ) -> SyncSaleProjection:
        result = await self.session.execute(
            insert(SyncSaleProjection)
            .values(
                sale_id=sale_id,
                tenant_id=tenant_id,
                branch_id=branch_id,
                origin_node_id=origin_node_id,
                writer_epoch=writer_epoch,
                sequence=sequence,
                source_event_id=source_event_id,
                operation_id=operation_id,
                register_id=register_id,
                shift_id=shift_id,
                cashier_user_id=cashier_user_id,
                receipt_number=receipt_number,
                receipt_seq=receipt_seq,
                sale_created_at=sale_created_at,
                completed_at=completed_at,
                total_amount=total_amount,
                currency=currency,
                is_test=is_test,
                items=items,
                payments=payments,
                source_payload_hash=source_payload_hash,
                projection_hash=projection_hash,
            )
            .returning(SyncSaleProjection)
        )
        return result.scalar_one()

    async def list_sale_projections(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        origin_node_id: UUID,
        writer_epoch: int,
        after_sequence: int,
    ) -> list[SyncSaleProjection]:
        result = await self.session.execute(
            select(SyncSaleProjection)
            .where(
                SyncSaleProjection.tenant_id == tenant_id,
                SyncSaleProjection.branch_id == branch_id,
                SyncSaleProjection.origin_node_id == origin_node_id,
                SyncSaleProjection.writer_epoch == writer_epoch,
                SyncSaleProjection.sequence > after_sequence,
            )
            .order_by(SyncSaleProjection.sequence)
        )
        return list(result.scalars().all())
