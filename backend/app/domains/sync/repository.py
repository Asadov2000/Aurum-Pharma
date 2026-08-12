"""Database access for Cloud outbox, Edge inbox, and shadow checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.foundation.models import Branch, Register, Tenant, TenantSettings
from app.domains.sync.models import (
    SyncActivationBootstrap,
    SyncActivationFoundation,
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


@dataclass(frozen=True, slots=True)
class ActivationFoundationSource:
    tenant: Tenant
    settings: TenantSettings
    branch: Branch
    register: Register


SyncHealth = Literal["healthy", "delayed", "offline", "critical", "revoked"]
SyncContactState = Literal["recent", "stale", "offline", "never_seen"]
SyncIntegrityState = Literal["verified", "stale_report", "unverified", "mismatch"]


@dataclass(frozen=True, slots=True)
class SyncMonitoringRow:
    node_id: UUID
    tenant_id: UUID
    tenant_name: str
    branch_id: UUID
    branch_name: str
    register_id: UUID | None
    register_name: str | None
    display_name: str
    mode: str
    node_status: str
    health: SyncHealth
    contact_state: SyncContactState
    integrity_state: SyncIntegrityState
    credential_expires_at: datetime
    last_seen_at: datetime | None
    latest_report_at: datetime | None
    latest_report_status: str | None
    source_verified: bool | None
    writer_epoch: int
    current_sequence: int
    reported_sequence: int | None
    lag_events: int
    lifecycle_version: int
    credential_rotation_id: UUID | None
    credential_rotation_status: str | None
    credential_rotation_activate_before: datetime | None
    credential_rotation_verified_at: datetime | None


@dataclass(frozen=True, slots=True)
class SyncMonitoringSummary:
    total_nodes: int
    healthy_nodes: int
    delayed_nodes: int
    offline_nodes: int
    critical_nodes: int
    revoked_nodes: int
    never_connected_nodes: int
    expiring_credentials: int
    pending_handovers: int
    pending_credential_rotations: int


@dataclass(frozen=True, slots=True)
class SyncCredentialRotationRecord:
    rotation_id: UUID
    node_id: UUID
    rotation_status: str
    node_version: int
    credential_issued_at: datetime
    credential_expires_at: datetime
    activate_before: datetime
    verified_at: datetime | None
    applied: bool


@dataclass(frozen=True, slots=True)
class SyncCredentialRotationTransitionRecord:
    rotation_id: UUID
    node_id: UUID
    rotation_status: str
    node_status: str
    node_version: int
    applied: bool


@dataclass(frozen=True, slots=True)
class SyncNodeLifecycleRecord:
    node_id: UUID
    node_status: str
    node_version: int
    applied: bool


@dataclass(frozen=True, slots=True)
class SyncTenantScope:
    tenant_id: UUID
    tenant_name: str
    node_count: int


_MONITORING_BASE_SQL = """
WITH latest_report AS (
  SELECT DISTINCT ON (report.edge_node_id)
    report.edge_node_id,
    report.last_sequence,
    report.source_verified,
    report.origin_node_id,
    report.writer_epoch,
    report.status,
    report.created_at
  FROM public.sync_shadow_report AS report
  ORDER BY report.edge_node_id, report.created_at DESC, report.report_id DESC
), open_rotation AS (
  SELECT
    rotation.id,
    rotation.node_id,
    CASE
      WHEN rotation.activate_before <= pg_catalog.now() THEN 'expired'
      ELSE rotation.status
    END AS status,
    rotation.activate_before,
    rotation.verified_at
  FROM public.sync_node_credential_rotation AS rotation
  WHERE rotation.status IN ('pending', 'verified')
), monitored AS (
  SELECT
    node.id AS node_id,
    node.tenant_id,
    tenant.name AS tenant_name,
    node.branch_id,
    branch.name AS branch_name,
    node.register_id,
    register.name AS register_name,
    node.display_name,
    node.mode,
    node.status AS node_status,
    node.lifecycle_version,
    node.credential_expires_at,
    rotation.id AS credential_rotation_id,
    rotation.status AS credential_rotation_status,
    rotation.activate_before AS credential_rotation_activate_before,
    rotation.verified_at AS credential_rotation_verified_at,
    node.last_seen_at,
    report.created_at AS latest_report_at,
    report.status AS latest_report_status,
    report.source_verified,
    stream.writer_epoch,
    stream.last_sequence AS current_sequence,
    report.last_sequence AS reported_sequence,
    GREATEST(stream.last_sequence - COALESCE(report.last_sequence, 0), 0) AS lag_events,
    CASE
      WHEN node.last_seen_at IS NULL THEN 'never_seen'
      WHEN node.last_seen_at < pg_catalog.now() - INTERVAL '30 minutes' THEN 'offline'
      WHEN node.last_seen_at < pg_catalog.now() - INTERVAL '5 minutes' THEN 'stale'
      ELSE 'recent'
    END AS contact_state,
    CASE
      WHEN report.status = 'mismatch' OR report.source_verified IS FALSE
        OR (
          report.edge_node_id IS NOT NULL
          AND (
            report.origin_node_id IS DISTINCT FROM stream.writer_node_id
            OR report.writer_epoch IS DISTINCT FROM stream.writer_epoch
          )
        ) THEN 'mismatch'
      WHEN report.edge_node_id IS NULL THEN 'unverified'
      WHEN report.created_at < pg_catalog.now() - INTERVAL '10 minutes'
        OR stream.last_sequence > report.last_sequence THEN 'stale_report'
      ELSE 'verified'
    END AS integrity_state,
    CASE
      WHEN node.status = 'revoked' THEN 'revoked'
      WHEN node.credential_expires_at <= pg_catalog.now()
        OR report.status = 'mismatch'
        OR report.source_verified IS FALSE
        OR (
          report.edge_node_id IS NOT NULL
          AND (
            report.origin_node_id IS DISTINCT FROM stream.writer_node_id
            OR report.writer_epoch IS DISTINCT FROM stream.writer_epoch
          )
        ) THEN 'critical'
      WHEN node.last_seen_at IS NULL
        OR node.last_seen_at < pg_catalog.now() - INTERVAL '30 minutes' THEN 'offline'
      WHEN report.edge_node_id IS NULL
        OR node.last_seen_at < pg_catalog.now() - INTERVAL '5 minutes'
        OR report.created_at < pg_catalog.now() - INTERVAL '10 minutes'
        OR stream.last_sequence > report.last_sequence
        OR node.credential_expires_at <= pg_catalog.now() + INTERVAL '7 days' THEN 'delayed'
      ELSE 'healthy'
    END AS health
  FROM public.sync_node AS node
  JOIN public.tenant AS tenant ON tenant.id = node.tenant_id
  JOIN public.branch AS branch
    ON branch.id = node.branch_id AND branch.tenant_id = node.tenant_id
  LEFT JOIN public.register AS register
    ON register.id = node.register_id AND register.tenant_id = node.tenant_id
  JOIN public.sync_stream AS stream
    ON stream.tenant_id = node.tenant_id AND stream.branch_id = node.branch_id
  LEFT JOIN latest_report AS report ON report.edge_node_id = node.id
  LEFT JOIN open_rotation AS rotation ON rotation.node_id = node.id
  WHERE node.node_kind = 'edge'
    AND (CAST(:tenant_id AS UUID) IS NULL OR node.tenant_id = CAST(:tenant_id AS UUID))
)
"""

_MONITORING_LIST_SQL = _MONITORING_BASE_SQL + """
SELECT monitored.*
FROM monitored
WHERE (CAST(:health AS TEXT) IS NULL OR monitored.health = CAST(:health AS TEXT))
  AND (CAST(:mode AS TEXT) IS NULL OR monitored.mode = CAST(:mode AS TEXT))
  AND (
    CAST(:query AS TEXT) IS NULL
    OR monitored.display_name ILIKE CAST(:query AS TEXT)
    OR monitored.tenant_name ILIKE CAST(:query AS TEXT)
    OR monitored.branch_name ILIKE CAST(:query AS TEXT)
    OR COALESCE(monitored.register_name, '') ILIKE CAST(:query AS TEXT)
  )
ORDER BY
  CASE monitored.health
    WHEN 'critical' THEN 1
    WHEN 'offline' THEN 2
    WHEN 'delayed' THEN 3
    WHEN 'healthy' THEN 4
    ELSE 5
  END,
  monitored.last_seen_at ASC NULLS FIRST,
  monitored.node_id
LIMIT :limit OFFSET :offset
"""

_MONITORING_COUNT_SQL = _MONITORING_BASE_SQL + """
SELECT COUNT(*)
FROM monitored
WHERE (CAST(:health AS TEXT) IS NULL OR monitored.health = CAST(:health AS TEXT))
  AND (CAST(:mode AS TEXT) IS NULL OR monitored.mode = CAST(:mode AS TEXT))
  AND (
    CAST(:query AS TEXT) IS NULL
    OR monitored.display_name ILIKE CAST(:query AS TEXT)
    OR monitored.tenant_name ILIKE CAST(:query AS TEXT)
    OR monitored.branch_name ILIKE CAST(:query AS TEXT)
    OR COALESCE(monitored.register_name, '') ILIKE CAST(:query AS TEXT)
  )
"""

_MONITORING_SUMMARY_SQL = _MONITORING_BASE_SQL + """
SELECT
  COUNT(*) AS total_nodes,
  COUNT(*) FILTER (WHERE health = 'healthy') AS healthy_nodes,
  COUNT(*) FILTER (WHERE health = 'delayed') AS delayed_nodes,
  COUNT(*) FILTER (WHERE health = 'offline') AS offline_nodes,
  COUNT(*) FILTER (WHERE health = 'critical') AS critical_nodes,
  COUNT(*) FILTER (WHERE health = 'revoked') AS revoked_nodes,
  COUNT(*) FILTER (WHERE last_seen_at IS NULL AND node_status = 'active')
    AS never_connected_nodes,
  COUNT(*) FILTER (
    WHERE node_status = 'active'
      AND credential_expires_at <= pg_catalog.now() + INTERVAL '7 days'
  ) AS expiring_credentials,
  (
    SELECT COUNT(*) FROM public.sync_writer_activation AS activation
    WHERE activation.state IN ('prepared', 'ready')
      AND (
        CAST(:tenant_id AS UUID) IS NULL
        OR activation.tenant_id = CAST(:tenant_id AS UUID)
      )
  ) AS pending_handovers,
  COUNT(*) FILTER (WHERE credential_rotation_id IS NOT NULL)
    AS pending_credential_rotations
FROM monitored
"""

_MONITORING_TENANTS_SQL = """
SELECT tenant.id AS tenant_id, tenant.name AS tenant_name, COUNT(node.id) AS node_count
FROM public.sync_node AS node
JOIN public.tenant AS tenant ON tenant.id = node.tenant_id
WHERE node.node_kind = 'edge'
GROUP BY tenant.id, tenant.name
ORDER BY tenant.name, tenant.id
"""


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

    async def list_monitoring_nodes(
        self,
        *,
        tenant_id: UUID | None,
        health: SyncHealth | None,
        mode: str | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[SyncMonitoringRow], int]:
        parameters = {
            "tenant_id": tenant_id,
            "health": health,
            "mode": mode,
            "query": f"%{query}%" if query else None,
        }
        total = int(
            (
                await self.session.execute(
                    text(_MONITORING_COUNT_SQL),
                    parameters,
                )
            ).scalar_one()
        )
        result = await self.session.execute(
            text(_MONITORING_LIST_SQL),
            {
                **parameters,
                "limit": limit,
                "offset": offset,
            },
        )
        mappings = result.mappings().all()
        rows = [
            SyncMonitoringRow(
                node_id=row["node_id"],
                tenant_id=row["tenant_id"],
                tenant_name=str(row["tenant_name"]),
                branch_id=row["branch_id"],
                branch_name=str(row["branch_name"]),
                register_id=row["register_id"],
                register_name=(str(row["register_name"]) if row["register_name"] else None),
                display_name=str(row["display_name"]),
                mode=str(row["mode"]),
                node_status=str(row["node_status"]),
                health=cast(SyncHealth, str(row["health"])),
                contact_state=cast(SyncContactState, str(row["contact_state"])),
                integrity_state=cast(SyncIntegrityState, str(row["integrity_state"])),
                credential_expires_at=row["credential_expires_at"],
                last_seen_at=row["last_seen_at"],
                latest_report_at=row["latest_report_at"],
                latest_report_status=(
                    str(row["latest_report_status"])
                    if row["latest_report_status"] is not None
                    else None
                ),
                source_verified=row["source_verified"],
                writer_epoch=int(row["writer_epoch"]),
                current_sequence=int(row["current_sequence"]),
                reported_sequence=(
                    int(row["reported_sequence"]) if row["reported_sequence"] is not None else None
                ),
                lag_events=int(row["lag_events"]),
                lifecycle_version=int(row["lifecycle_version"]),
                credential_rotation_id=row["credential_rotation_id"],
                credential_rotation_status=(
                    str(row["credential_rotation_status"])
                    if row["credential_rotation_status"] is not None
                    else None
                ),
                credential_rotation_activate_before=row["credential_rotation_activate_before"],
                credential_rotation_verified_at=row["credential_rotation_verified_at"],
            )
            for row in mappings
        ]
        return rows, total

    async def monitoring_summary(
        self,
        *,
        tenant_id: UUID | None,
    ) -> SyncMonitoringSummary:
        row = (
            (
                await self.session.execute(
                    text(_MONITORING_SUMMARY_SQL),
                    {"tenant_id": tenant_id},
                )
            )
            .mappings()
            .one()
        )
        return SyncMonitoringSummary(**{key: int(value) for key, value in row.items()})

    async def list_monitoring_tenants(self) -> list[SyncTenantScope]:
        result = await self.session.execute(text(_MONITORING_TENANTS_SQL))
        return [
            SyncTenantScope(
                tenant_id=row["tenant_id"],
                tenant_name=str(row["tenant_name"]),
                node_count=int(row["node_count"]),
            )
            for row in result.mappings().all()
        ]

    async def get_edge_node(self, node_id: UUID) -> SyncNode | None:
        result = await self.session.execute(
            select(SyncNode).where(SyncNode.id == node_id, SyncNode.node_kind == "edge")
        )
        return result.scalar_one_or_none()

    async def prepare_credential_rotation(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        node_id: UUID,
        expected_version: int,
        operation_id: UUID,
        credential_kid: UUID,
        credential_hash: str,
        credential_expires_at: datetime,
        confirmation_name: str,
        request_hash: str,
        reason_code: str,
        reason: str,
    ) -> SyncCredentialRotationRecord | None:
        row = (
            (
                await self.session.execute(
                    text("""
                    SELECT * FROM public.prepare_sync_node_credential_rotation(
                      :actor_user_id, :actor_session_id, :node_id, :expected_version,
                      :operation_id, :credential_kid, :credential_hash,
                      :credential_expires_at, :confirmation_name, :request_hash,
                      :reason_code, :reason
                    )
                    """),
                    {
                        "actor_user_id": actor_user_id,
                        "actor_session_id": actor_session_id,
                        "node_id": node_id,
                        "expected_version": expected_version,
                        "operation_id": operation_id,
                        "credential_kid": credential_kid,
                        "credential_hash": credential_hash,
                        "credential_expires_at": credential_expires_at,
                        "confirmation_name": confirmation_name,
                        "request_hash": request_hash,
                        "reason_code": reason_code,
                        "reason": reason,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return SyncCredentialRotationRecord(
            rotation_id=row["rotation_id"],
            node_id=row["node_id"],
            rotation_status=str(row["rotation_status"]),
            node_version=int(row["node_version"]),
            credential_issued_at=row["credential_issued_at"],
            credential_expires_at=row["credential_expires_at"],
            activate_before=row["activate_before"],
            verified_at=row["verified_at"],
            applied=bool(row["applied"]),
        )

    async def transition_credential_rotation(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        rotation_id: UUID,
        expected_version: int,
        operation_id: UUID,
        action: str,
        confirmation_name: str,
        request_hash: str,
        reason_code: str,
        reason: str,
    ) -> SyncCredentialRotationTransitionRecord | None:
        row = (
            (
                await self.session.execute(
                    text("""
                    SELECT * FROM public.transition_sync_node_credential_rotation(
                      :actor_user_id, :actor_session_id, :rotation_id, :expected_version,
                      :operation_id, :action, :confirmation_name, :request_hash,
                      :reason_code, :reason
                    )
                    """),
                    {
                        "actor_user_id": actor_user_id,
                        "actor_session_id": actor_session_id,
                        "rotation_id": rotation_id,
                        "expected_version": expected_version,
                        "operation_id": operation_id,
                        "action": action,
                        "confirmation_name": confirmation_name,
                        "request_hash": request_hash,
                        "reason_code": reason_code,
                        "reason": reason,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return SyncCredentialRotationTransitionRecord(
            rotation_id=row["rotation_id"],
            node_id=row["node_id"],
            rotation_status=str(row["rotation_status"]),
            node_status=str(row["node_status"]),
            node_version=int(row["node_version"]),
            applied=bool(row["applied"]),
        )

    async def revoke_node_safely(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        node_id: UUID,
        expected_version: int,
        operation_id: UUID,
        confirmation_name: str,
        request_hash: str,
        reason_code: str,
        reason: str,
    ) -> SyncNodeLifecycleRecord | None:
        row = (
            (
                await self.session.execute(
                    text("""
                    SELECT * FROM public.revoke_sync_node(
                      :actor_user_id, :actor_session_id, :node_id, :expected_version,
                      :operation_id, :confirmation_name, :request_hash,
                      :reason_code, :reason
                    )
                    """),
                    {
                        "actor_user_id": actor_user_id,
                        "actor_session_id": actor_session_id,
                        "node_id": node_id,
                        "expected_version": expected_version,
                        "operation_id": operation_id,
                        "confirmation_name": confirmation_name,
                        "request_hash": request_hash,
                        "reason_code": reason_code,
                        "reason": reason,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return SyncNodeLifecycleRecord(
            node_id=row["node_id"],
            node_status=str(row["node_status"]),
            node_version=int(row["node_version"]),
            applied=bool(row["applied"]),
        )

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

    async def get_activation_bootstrap(
        self,
        activation_id: UUID,
    ) -> SyncActivationBootstrap | None:
        result = await self.session.execute(
            select(SyncActivationBootstrap)
            .where(SyncActivationBootstrap.activation_id == activation_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_activation_foundation(
        self,
        activation_id: UUID,
    ) -> SyncActivationFoundation | None:
        result = await self.session.execute(
            select(SyncActivationFoundation)
            .where(SyncActivationFoundation.activation_id == activation_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_activation_foundation_source(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        register_id: UUID,
    ) -> ActivationFoundationSource | None:
        tenant = await self.session.scalar(
            select(Tenant).where(Tenant.id == tenant_id).with_for_update(read=True)
        )
        settings = await self.session.scalar(
            select(TenantSettings)
            .where(TenantSettings.tenant_id == tenant_id)
            .with_for_update(read=True)
        )
        branch = await self.session.scalar(
            select(Branch)
            .where(Branch.id == branch_id, Branch.tenant_id == tenant_id)
            .with_for_update(read=True)
        )
        register = await self.session.scalar(
            select(Register)
            .where(
                Register.id == register_id,
                Register.tenant_id == tenant_id,
                Register.branch_id == branch_id,
            )
            .with_for_update(read=True)
        )
        if tenant is None or settings is None or branch is None or register is None:
            return None
        return ActivationFoundationSource(
            tenant=tenant,
            settings=settings,
            branch=branch,
            register=register,
        )

    async def persist_activation_foundation(
        self,
        *,
        activation: SyncWriterActivation,
        foundation_payload: dict[str, object],
        foundation_hash: str,
        snapshot_hash: str,
    ) -> tuple[SyncActivationBootstrap, SyncActivationFoundation]:
        await self.session.execute(
            pg_insert(SyncActivationBootstrap)
            .values(
                activation_id=activation.activation_id,
                tenant_id=activation.tenant_id,
                branch_id=activation.branch_id,
                edge_node_id=activation.writer_node_id,
                register_id=activation.allowed_register_id,
                writer_epoch=activation.writer_epoch,
                capability=activation.capability,
                profile="foundation_shadow_v1",
                readiness_eligible=False,
                foundation_hash=foundation_hash,
                snapshot_hash=snapshot_hash,
                activation_manifest_hash=activation.activation_manifest_hash,
            )
            .on_conflict_do_nothing(index_elements=[SyncActivationBootstrap.activation_id])
        )
        await self.session.execute(
            pg_insert(SyncActivationFoundation)
            .values(
                activation_id=activation.activation_id,
                tenant_id=activation.tenant_id,
                branch_id=activation.branch_id,
                edge_node_id=activation.writer_node_id,
                register_id=activation.allowed_register_id,
                writer_epoch=activation.writer_epoch,
                schema_version=1,
                payload=foundation_payload,
                payload_hash=foundation_hash,
            )
            .on_conflict_do_nothing(index_elements=[SyncActivationFoundation.activation_id])
        )
        bootstrap = await self.get_activation_bootstrap(activation.activation_id)
        foundation = await self.get_activation_foundation(activation.activation_id)
        if bootstrap is None or foundation is None:
            raise RuntimeError("Activation foundation bootstrap was not persisted")
        if (
            bootstrap.tenant_id != activation.tenant_id
            or bootstrap.branch_id != activation.branch_id
            or bootstrap.edge_node_id != activation.writer_node_id
            or bootstrap.register_id != activation.allowed_register_id
            or bootstrap.writer_epoch != activation.writer_epoch
            or bootstrap.capability != activation.capability
            or bootstrap.profile != "foundation_shadow_v1"
            or bootstrap.readiness_eligible
            or bootstrap.foundation_hash != foundation_hash
            or bootstrap.snapshot_hash != snapshot_hash
            or bootstrap.activation_manifest_hash != activation.activation_manifest_hash
            or foundation.tenant_id != activation.tenant_id
            or foundation.branch_id != activation.branch_id
            or foundation.edge_node_id != activation.writer_node_id
            or foundation.register_id != activation.allowed_register_id
            or foundation.writer_epoch != activation.writer_epoch
            or foundation.schema_version != 1
            or foundation.payload_hash != foundation_hash
            or foundation.payload != foundation_payload
        ):
            raise RuntimeError("Activation foundation bootstrap is inconsistent")
        return bootstrap, foundation

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
        foundation_hash: str,
        request_hash: str,
    ) -> SyncWriterActivation:
        result = await self.session.execute(
            text(
                "SELECT public.prepare_edge_writer_foundation_handover("
                ":activation_id, :tenant_id, :branch_id, :edge_node_id, :register_id, "
                ":expected_writer_epoch, :expected_sequence, :expected_source_checksum, "
                ":expected_projection_checksum, :foundation_hash, :request_hash)"
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
                "foundation_hash": foundation_hash,
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
