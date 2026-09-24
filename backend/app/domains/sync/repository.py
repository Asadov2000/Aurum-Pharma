"""Database access for Cloud outbox, Edge inbox, and shadow checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.catalog.models import Barcode, TenantCatalog
from app.domains.foundation.models import Branch, Register, Tenant, TenantSettings
from app.domains.inventory.models import Batch
from app.domains.suppliers.models import Supplier
from app.domains.sync.models import (
    SyncActivationBootstrap,
    SyncActivationFoundation,
    SyncCursor,
    SyncInboxEvent,
    SyncNode,
    SyncOutboxEvent,
    SyncQuarantineIncident,
    SyncReadBarcodeProjection,
    SyncReadCatalogProjection,
    SyncReadInventoryProjection,
    SyncReadInventoryRollup,
    SyncReadProjection,
    SyncReadSnapshot,
    SyncReadSnapshotChunk,
    SyncReadSnapshotComponent,
    SyncReadSnapshotCursor,
    SyncReadSupplierProjection,
    SyncSaleProjection,
    SyncShadowReport,
    SyncStream,
    SyncWriterActivation,
    SyncWriterEpoch,
    SyncWriterReadiness,
)
from app.domains.sync.read_snapshot import ChunkRead, ManifestRead
from app.domains.sync.read_snapshot_projection import IndexedReadProjection
from app.domains.sync.schemas import SyncEventEnvelope, SyncQuarantineIncidentRequest


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


class SyncProjectionOperationCollision(RuntimeError):
    """The tenant-scoped operation ID is already owned by another projection."""


@dataclass(frozen=True, slots=True)
class InventoryProjectionSummary:
    total_qty: Decimal
    purchase_value: Decimal
    sale_value: Decimal
    attention_count: int
    expired_count: int
    blocked_count: int


def _is_projection_operation_collision(exc: IntegrityError) -> bool:
    if getattr(exc.orig, "sqlstate", None) != "23505":
        return False
    cause = getattr(exc.orig, "__cause__", None)
    constraint_name = getattr(exc.orig, "constraint_name", None) or getattr(
        cause, "constraint_name", None
    )
    return constraint_name == "uq_sync_sale_projection_operation" or (
        "uq_sync_sale_projection_operation" in str(exc)
    )


def _substring_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


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
    quarantine_incident_count: int
    latest_quarantine_reason: str | None
    latest_quarantine_status: str | None
    latest_quarantine_sequence: int | None
    latest_quarantine_at: datetime | None


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
    quarantined_nodes: int


@dataclass(frozen=True, slots=True)
class SyncQuarantineIncidentRecord:
    incident_id: UUID
    tenant_id: UUID
    branch_id: UUID
    edge_node_id: UUID
    origin_node_id: UUID
    writer_epoch: int
    cursor_status: str
    reason_code: str
    last_applied_sequence: int
    blocked_sequence: int | None
    blocked_event_id: UUID | None
    blocked_operation_id: UUID | None
    source_checksum: str
    projection_checksum: str
    event_type: str | None
    schema_version: int | None
    observed_at: datetime
    evidence_hash: str
    received_at: datetime
    replayed: bool


async def _record_quarantine_incident(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    branch_id: UUID,
    edge_node_id: UUID,
    payload: SyncQuarantineIncidentRequest,
    request_hash: str,
) -> SyncQuarantineIncidentRecord:
    row = (
        (
            await session.execute(
                text("""
                SELECT * FROM public.record_sync_quarantine_incident(
                  :incident_id, :tenant_id, :branch_id, :edge_node_id,
                  :origin_node_id, :writer_epoch, :cursor_status, :reason_code,
                  :last_applied_sequence, :blocked_sequence, :blocked_event_id,
                  :blocked_operation_id, :source_checksum, :projection_checksum,
                  :event_type, :schema_version, :observed_at, :evidence_hash,
                  :request_hash
                )
                """),
                {
                    **payload.model_dump(),
                    "tenant_id": tenant_id,
                    "branch_id": branch_id,
                    "edge_node_id": edge_node_id,
                    "request_hash": request_hash,
                },
            )
        )
        .mappings()
        .one()
    )
    return SyncQuarantineIncidentRecord(
        incident_id=row["incident_id"],
        tenant_id=row["tenant_id"],
        branch_id=row["branch_id"],
        edge_node_id=row["edge_node_id"],
        origin_node_id=row["origin_node_id"],
        writer_epoch=int(row["writer_epoch"]),
        cursor_status=str(row["cursor_status"]),
        reason_code=str(row["reason_code"]),
        last_applied_sequence=int(row["last_applied_sequence"]),
        blocked_sequence=(
            int(row["blocked_sequence"]) if row["blocked_sequence"] is not None else None
        ),
        blocked_event_id=row["blocked_event_id"],
        blocked_operation_id=row["blocked_operation_id"],
        source_checksum=str(row["source_checksum"]),
        projection_checksum=str(row["projection_checksum"]),
        event_type=(str(row["event_type"]) if row["event_type"] is not None else None),
        schema_version=(int(row["schema_version"]) if row["schema_version"] is not None else None),
        observed_at=row["observed_at"],
        evidence_hash=str(row["evidence_hash"]),
        received_at=row["received_at"],
        replayed=bool(row["replayed"]),
    )


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
), incident_rollup AS (
  SELECT
    incident.edge_node_id,
    COUNT(*) AS incident_count,
    (ARRAY_AGG(incident.reason_code ORDER BY incident.received_at DESC, incident.incident_id))[1]
      AS latest_reason,
    (ARRAY_AGG(incident.cursor_status ORDER BY incident.received_at DESC, incident.incident_id))[1]
      AS latest_status,
    (ARRAY_AGG(
      incident.last_applied_sequence
      ORDER BY incident.received_at DESC, incident.incident_id
    ))[1]
      AS latest_sequence,
    MAX(incident.received_at) AS latest_at
  FROM public.sync_quarantine_incident AS incident
  GROUP BY incident.edge_node_id
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
    COALESCE(incident.incident_count, 0) AS quarantine_incident_count,
    incident.latest_reason AS latest_quarantine_reason,
    incident.latest_status AS latest_quarantine_status,
    incident.latest_sequence AS latest_quarantine_sequence,
    incident.latest_at AS latest_quarantine_at,
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
      WHEN COALESCE(incident.incident_count, 0) > 0
        OR node.credential_expires_at <= pg_catalog.now()
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
  LEFT JOIN incident_rollup AS incident ON incident.edge_node_id = node.id
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
    AS pending_credential_rotations,
  COUNT(*) FILTER (
    WHERE node_status = 'active' AND quarantine_incident_count > 0
  ) AS quarantined_nodes
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

    async def lock_read_snapshot_scope(self, edge_node_id: UUID) -> None:
        """Serialize publishers without granting runtime access to sync_node."""

        await self.session.execute(
            text(
                "SELECT pg_catalog.pg_advisory_xact_lock("
                "pg_catalog.hashtextextended(:edge_node_id, 0))"
            ),
            {"edge_node_id": str(edge_node_id)},
        )

    async def next_read_snapshot_revision(
        self, *, tenant_id: UUID, branch_id: UUID, edge_node_id: UUID
    ) -> int:
        result = await self.session.execute(
            select(func.coalesce(func.max(SyncReadSnapshot.source_revision), 0)).where(
                SyncReadSnapshot.tenant_id == tenant_id,
                SyncReadSnapshot.branch_id == branch_id,
                SyncReadSnapshot.node_id == edge_node_id,
            )
        )
        return int(result.scalar_one()) + 1

    async def read_snapshot_foundation_source(
        self, *, tenant_id: UUID, branch_id: UUID
    ) -> tuple[Tenant, TenantSettings, Branch, list[Register]] | None:
        tenant = await self.session.get(Tenant, tenant_id)
        settings = await self.session.get(TenantSettings, tenant_id)
        branch_result = await self.session.execute(
            select(Branch).where(Branch.id == branch_id, Branch.tenant_id == tenant_id)
        )
        branch = branch_result.scalar_one_or_none()
        if tenant is None or settings is None or branch is None:
            return None
        registers_result = await self.session.execute(
            select(Register)
            .where(Register.tenant_id == tenant_id, Register.branch_id == branch_id)
            .order_by(Register.id)
        )
        return tenant, settings, branch, list(registers_result.scalars().all())

    async def read_snapshot_catalog_source(
        self, *, tenant_id: UUID
    ) -> tuple[list[TenantCatalog], dict[UUID, list[Barcode]], list[Supplier]]:
        catalog_result = await self.session.execute(
            select(TenantCatalog)
            .where(TenantCatalog.tenant_id == tenant_id)
            .order_by(TenantCatalog.id)
        )
        barcode_result = await self.session.execute(
            select(Barcode)
            .where(Barcode.tenant_id == tenant_id)
            .order_by(Barcode.catalog_id, Barcode.id)
        )
        supplier_result = await self.session.execute(
            select(Supplier).where(Supplier.tenant_id == tenant_id).order_by(Supplier.id)
        )
        barcodes: dict[UUID, list[Barcode]] = {}
        for barcode in barcode_result.scalars().all():
            barcodes.setdefault(barcode.catalog_id, []).append(barcode)
        return (
            list(catalog_result.scalars().all()),
            barcodes,
            list(supplier_result.scalars().all()),
        )

    async def read_snapshot_inventory_source(
        self, *, tenant_id: UUID, branch_id: UUID
    ) -> list[tuple[Batch, str, TenantCatalog]]:
        result = await self.session.execute(
            select(Batch, Branch.name, TenantCatalog)
            .join(
                Branch,
                (Branch.id == Batch.branch_id) & (Branch.tenant_id == Batch.tenant_id),
            )
            .join(
                TenantCatalog,
                (TenantCatalog.id == Batch.catalog_id)
                & (TenantCatalog.tenant_id == Batch.tenant_id),
            )
            .where(Batch.tenant_id == tenant_id, Batch.branch_id == branch_id)
            .order_by(Batch.id)
        )
        return [(row[0], str(row[1]), row[2]) for row in result.all()]

    async def publish_read_snapshot(
        self, *, envelope: ManifestRead, chunks: list[ChunkRead]
    ) -> None:
        """Persist a complete Cloud snapshot; readers never observe the draft."""

        manifest = envelope.manifest
        scope_values = {
            "snapshot_id": manifest.snapshot_id,
            "tenant_id": manifest.tenant_id,
            "branch_id": manifest.branch_id,
            "node_id": manifest.edge_node_id,
            "source_revision": manifest.source_revision,
        }
        await self.session.execute(
            insert(SyncReadSnapshot).values(
                **scope_values,
                status="draft",
                schema_version=manifest.schema_version,
                credential_kid=manifest.credential_kid,
                manifest_hash=envelope.manifest_hash,
                snapshot_hash=manifest.snapshot_hash,
                signature_algorithm=envelope.signature_algorithm,
                signature=envelope.signature,
                source_generated_at=manifest.source_generated_at,
            )
        )
        for component in manifest.components:
            await self.session.execute(
                insert(SyncReadSnapshotComponent).values(
                    **scope_values,
                    component=component.component,
                    schema_version=component.schema_version,
                    item_count=component.item_count,
                    chunk_count=component.chunk_count,
                    component_hash=component.component_hash,
                )
            )
        for chunk in chunks:
            await self.session.execute(
                insert(SyncReadSnapshotChunk).values(
                    **scope_values,
                    component=chunk.component,
                    chunk_index=chunk.chunk_index,
                    schema_version=chunk.schema_version,
                    item_count=chunk.item_count,
                    payload={"items": list(chunk.payload)},
                    payload_hash=chunk.payload_hash,
                )
            )
        await self.session.execute(
            update(SyncReadSnapshot)
            .where(
                SyncReadSnapshot.snapshot_id == manifest.snapshot_id,
                SyncReadSnapshot.tenant_id == manifest.tenant_id,
                SyncReadSnapshot.branch_id == manifest.branch_id,
                SyncReadSnapshot.node_id == manifest.edge_node_id,
            )
            .values(status="ready")
        )

    async def get_latest_read_snapshot(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        edge_node_id: UUID,
        credential_kid: UUID,
    ) -> tuple[SyncReadSnapshot, list[SyncReadSnapshotComponent]] | None:
        result = await self.session.execute(
            select(SyncReadSnapshot)
            .where(
                SyncReadSnapshot.tenant_id == tenant_id,
                SyncReadSnapshot.branch_id == branch_id,
                SyncReadSnapshot.node_id == edge_node_id,
                SyncReadSnapshot.credential_kid == credential_kid,
                SyncReadSnapshot.status == "ready",
            )
            .order_by(SyncReadSnapshot.source_revision.desc())
            .limit(1)
        )
        snapshot = result.scalar_one_or_none()
        if snapshot is None:
            return None
        component_result = await self.session.execute(
            select(SyncReadSnapshotComponent)
            .where(
                SyncReadSnapshotComponent.snapshot_id == snapshot.snapshot_id,
                SyncReadSnapshotComponent.tenant_id == tenant_id,
                SyncReadSnapshotComponent.branch_id == branch_id,
                SyncReadSnapshotComponent.node_id == edge_node_id,
            )
            .order_by(SyncReadSnapshotComponent.component)
        )
        return snapshot, list(component_result.scalars().all())

    async def list_read_snapshot_chunks(
        self,
        *,
        snapshot_id: UUID,
        tenant_id: UUID,
        branch_id: UUID,
        edge_node_id: UUID,
    ) -> list[SyncReadSnapshotChunk]:
        result = await self.session.execute(
            select(SyncReadSnapshotChunk)
            .where(
                SyncReadSnapshotChunk.snapshot_id == snapshot_id,
                SyncReadSnapshotChunk.tenant_id == tenant_id,
                SyncReadSnapshotChunk.branch_id == branch_id,
                SyncReadSnapshotChunk.node_id == edge_node_id,
            )
            .order_by(
                SyncReadSnapshotChunk.component,
                SyncReadSnapshotChunk.chunk_index,
            )
        )
        return list(result.scalars().all())

    async def get_read_snapshot_chunk(
        self,
        *,
        snapshot_id: UUID,
        tenant_id: UUID,
        branch_id: UUID,
        edge_node_id: UUID,
        component: str,
        chunk_index: int,
    ) -> SyncReadSnapshotChunk | None:
        result = await self.session.execute(
            select(SyncReadSnapshotChunk).where(
                SyncReadSnapshotChunk.snapshot_id == snapshot_id,
                SyncReadSnapshotChunk.tenant_id == tenant_id,
                SyncReadSnapshotChunk.branch_id == branch_id,
                SyncReadSnapshotChunk.node_id == edge_node_id,
                SyncReadSnapshotChunk.component == component,
                SyncReadSnapshotChunk.chunk_index == chunk_index,
            )
        )
        return result.scalar_one_or_none()

    async def record_quarantine_incident(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        edge_node_id: UUID,
        payload: SyncQuarantineIncidentRequest,
        request_hash: str,
    ) -> SyncQuarantineIncidentRecord:
        return await _record_quarantine_incident(
            self.session,
            tenant_id=tenant_id,
            branch_id=branch_id,
            edge_node_id=edge_node_id,
            payload=payload,
            request_hash=request_hash,
        )

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
                quarantine_incident_count=int(row["quarantine_incident_count"]),
                latest_quarantine_reason=(
                    str(row["latest_quarantine_reason"])
                    if row["latest_quarantine_reason"] is not None
                    else None
                ),
                latest_quarantine_status=(
                    str(row["latest_quarantine_status"])
                    if row["latest_quarantine_status"] is not None
                    else None
                ),
                latest_quarantine_sequence=(
                    int(row["latest_quarantine_sequence"])
                    if row["latest_quarantine_sequence"] is not None
                    else None
                ),
                latest_quarantine_at=row["latest_quarantine_at"],
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

    async def get_active_read_snapshot_id(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        edge_node_id: UUID,
    ) -> UUID | None:
        result = await self.session.execute(
            select(SyncReadSnapshotCursor.active_snapshot_id).where(
                SyncReadSnapshotCursor.tenant_id == tenant_id,
                SyncReadSnapshotCursor.branch_id == branch_id,
                SyncReadSnapshotCursor.node_id == edge_node_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_active_read_projection(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        credential_kid: UUID,
    ) -> tuple[SyncReadSnapshot, SyncReadProjection, datetime] | None:
        result = await self.session.execute(
            select(
                SyncReadSnapshot,
                SyncReadProjection,
                SyncReadSnapshotCursor.last_confirmed_at,
            )
            .join(
                SyncReadSnapshotCursor,
                (SyncReadSnapshotCursor.active_snapshot_id == SyncReadSnapshot.snapshot_id)
                & (SyncReadSnapshotCursor.tenant_id == SyncReadSnapshot.tenant_id)
                & (SyncReadSnapshotCursor.branch_id == SyncReadSnapshot.branch_id)
                & (SyncReadSnapshotCursor.node_id == SyncReadSnapshot.node_id),
            )
            .join(
                SyncReadProjection,
                (SyncReadProjection.snapshot_id == SyncReadSnapshot.snapshot_id)
                & (SyncReadProjection.tenant_id == SyncReadSnapshot.tenant_id)
                & (SyncReadProjection.branch_id == SyncReadSnapshot.branch_id)
                & (SyncReadProjection.node_id == SyncReadSnapshot.node_id),
            )
            .where(
                SyncReadSnapshot.tenant_id == tenant_id,
                SyncReadSnapshot.branch_id == branch_id,
                SyncReadSnapshot.credential_kid == credential_kid,
                SyncReadSnapshot.status == "ready",
                SyncReadProjection.status == "ready",
            )
        )
        row = result.one_or_none()
        if row is None:
            return None
        return row[0], row[1], row[2]

    async def get_read_snapshot_verification_bundle(
        self,
        *,
        snapshot_id: UUID,
        tenant_id: UUID,
        branch_id: UUID,
        edge_node_id: UUID,
    ) -> tuple[list[SyncReadSnapshotComponent], list[SyncReadSnapshotChunk]]:
        scope = (
            SyncReadSnapshotComponent.snapshot_id == snapshot_id,
            SyncReadSnapshotComponent.tenant_id == tenant_id,
            SyncReadSnapshotComponent.branch_id == branch_id,
            SyncReadSnapshotComponent.node_id == edge_node_id,
        )
        components = await self.session.scalars(
            select(SyncReadSnapshotComponent)
            .where(*scope)
            .order_by(SyncReadSnapshotComponent.component)
        )
        chunks = await self.session.scalars(
            select(SyncReadSnapshotChunk)
            .where(
                SyncReadSnapshotChunk.snapshot_id == snapshot_id,
                SyncReadSnapshotChunk.tenant_id == tenant_id,
                SyncReadSnapshotChunk.branch_id == branch_id,
                SyncReadSnapshotChunk.node_id == edge_node_id,
            )
            .order_by(
                SyncReadSnapshotChunk.component,
                SyncReadSnapshotChunk.chunk_index,
            )
        )
        return list(components.all()), list(chunks.all())

    async def list_read_catalog_projection(
        self,
        *,
        snapshot_id: UUID,
        query: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[SyncReadCatalogProjection], int]:
        filters = [SyncReadCatalogProjection.snapshot_id == snapshot_id]
        if query:
            matched_catalog = func.search_sync_read_catalog(snapshot_id, query).table_valued(
                "catalog_id"
            )
            total = await self.session.scalar(select(func.count()).select_from(matched_catalog))
            matched_ids = await self.session.scalars(
                select(matched_catalog.c.catalog_id)
                .order_by(matched_catalog.c.catalog_id)
                .offset(offset)
                .limit(limit)
            )
            catalog_ids = list(matched_ids.all())
            if not catalog_ids:
                return [], int(total or 0)
            filters.append(SyncReadCatalogProjection.catalog_id.in_(catalog_ids))
            offset = 0
        else:
            # A ready projection's count was checked against its rows at activation.
            ready_total = await self.session.scalar(
                select(SyncReadProjection.catalog_count).where(
                    SyncReadProjection.snapshot_id == snapshot_id,
                    SyncReadProjection.status == "ready",
                )
            )
            total = (
                ready_total
                if ready_total is not None
                else await self.session.scalar(
                    select(func.count()).select_from(SyncReadCatalogProjection).where(*filters)
                )
            )
        rows = await self.session.scalars(
            select(SyncReadCatalogProjection)
            .where(*filters)
            .order_by(SyncReadCatalogProjection.catalog_id)
            .offset(offset)
            .limit(limit)
        )
        return list(rows.all()), int(total or 0)

    async def get_read_catalog_projection(
        self,
        *,
        snapshot_id: UUID,
        item_id: UUID | None,
        barcode: str | None,
    ) -> tuple[SyncReadCatalogProjection, list[SyncReadBarcodeProjection]] | None:
        statement = select(SyncReadCatalogProjection).where(
            SyncReadCatalogProjection.snapshot_id == snapshot_id
        )
        if item_id is not None:
            statement = statement.where(SyncReadCatalogProjection.catalog_id == item_id)
        else:
            statement = statement.join(
                SyncReadBarcodeProjection,
                (SyncReadBarcodeProjection.snapshot_id == SyncReadCatalogProjection.snapshot_id)
                & (SyncReadBarcodeProjection.catalog_id == SyncReadCatalogProjection.catalog_id),
            ).where(SyncReadBarcodeProjection.code == barcode)
        product = (await self.session.scalars(statement.limit(1))).one_or_none()
        if product is None:
            return None
        barcodes = await self.session.scalars(
            select(SyncReadBarcodeProjection)
            .where(
                SyncReadBarcodeProjection.snapshot_id == snapshot_id,
                SyncReadBarcodeProjection.catalog_id == product.catalog_id,
            )
            .order_by(SyncReadBarcodeProjection.code)
        )
        return product, list(barcodes.all())

    async def list_read_supplier_projection(
        self,
        *,
        snapshot_id: UUID,
        query: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[SyncReadSupplierProjection], int]:
        filters = [SyncReadSupplierProjection.snapshot_id == snapshot_id]
        if query:
            filters.append(
                SyncReadSupplierProjection.search_text.ilike(_substring_pattern(query), escape="\\")
            )
        total = await self.session.scalar(
            select(func.count()).select_from(SyncReadSupplierProjection).where(*filters)
        )
        rows = await self.session.scalars(
            select(SyncReadSupplierProjection)
            .where(*filters)
            .order_by(SyncReadSupplierProjection.supplier_id)
            .offset(offset)
            .limit(limit)
        )
        return list(rows.all()), int(total or 0)

    async def list_read_inventory_projection(
        self,
        *,
        snapshot_id: UUID,
        show_empty: bool,
        offset: int,
        limit: int,
        today: date,
        orange_until: date,
    ) -> tuple[list[SyncReadInventoryProjection], int, InventoryProjectionSummary]:
        filters = [SyncReadInventoryProjection.snapshot_id == snapshot_id]
        if not show_empty:
            filters.append(SyncReadInventoryProjection.qty_remaining > 0)
        page_query = select(SyncReadInventoryProjection.batch_id).where(*filters)
        if show_empty:
            page_query = page_query.order_by(SyncReadInventoryProjection.batch_id)
        else:
            page_query = page_query.order_by(
                SyncReadInventoryProjection.tenant_id,
                SyncReadInventoryProjection.branch_id,
                SyncReadInventoryProjection.node_id,
                SyncReadInventoryProjection.snapshot_id,
                SyncReadInventoryProjection.batch_id,
            )
        rollup_filters = [SyncReadInventoryRollup.snapshot_id == snapshot_id]
        if not show_empty:
            rollup_filters.append(SyncReadInventoryRollup.has_stock.is_(True))
        summary = (
            await self.session.execute(
                select(
                    func.coalesce(func.sum(SyncReadInventoryRollup.batch_count), 0),
                    func.coalesce(func.sum(SyncReadInventoryRollup.total_qty), 0),
                    func.coalesce(func.sum(SyncReadInventoryRollup.purchase_value), 0),
                    func.coalesce(func.sum(SyncReadInventoryRollup.sale_value), 0),
                    func.coalesce(
                        func.sum(SyncReadInventoryRollup.batch_count).filter(
                            SyncReadInventoryRollup.expires_at <= orange_until
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(SyncReadInventoryRollup.batch_count).filter(
                            SyncReadInventoryRollup.expires_at < today
                        ),
                        0,
                    ),
                    func.coalesce(func.sum(SyncReadInventoryRollup.blocked_count), 0),
                ).where(*rollup_filters)
            )
        ).one()
        batch_ids = list((await self.session.scalars(page_query.offset(offset).limit(limit))).all())
        rows = (
            await self.session.scalars(
                select(SyncReadInventoryProjection)
                .where(
                    SyncReadInventoryProjection.snapshot_id == snapshot_id,
                    SyncReadInventoryProjection.batch_id.in_(batch_ids),
                )
                .order_by(SyncReadInventoryProjection.batch_id)
            )
            if batch_ids
            else None
        )
        items = list(rows.all()) if rows is not None else []
        return (
            items,
            int(summary[0]),
            InventoryProjectionSummary(
                total_qty=Decimal(summary[1]),
                purchase_value=Decimal(summary[2]),
                sale_value=Decimal(summary[3]),
                attention_count=int(summary[4]),
                expired_count=int(summary[5]),
                blocked_count=int(summary[6]),
            ),
        )

    async def confirm_active_read_snapshot(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        edge_node_id: UUID,
        snapshot_id: UUID,
    ) -> None:
        await self.session.execute(
            update(SyncReadSnapshotCursor)
            .where(
                SyncReadSnapshotCursor.tenant_id == tenant_id,
                SyncReadSnapshotCursor.branch_id == branch_id,
                SyncReadSnapshotCursor.node_id == edge_node_id,
                SyncReadSnapshotCursor.active_snapshot_id == snapshot_id,
            )
            .values(
                last_confirmed_at=func.statement_timestamp(),
                updated_at=func.statement_timestamp(),
            )
        )

    async def install_read_snapshot(
        self,
        *,
        envelope: ManifestRead,
        chunks: list[ChunkRead],
        projection: IndexedReadProjection,
    ) -> None:
        manifest = envelope.manifest
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {
                "scope": (
                    f"edge-read:{manifest.tenant_id}:{manifest.branch_id}:"
                    f"{manifest.edge_node_id}"
                )
            },
        )
        scope_values = {
            "snapshot_id": manifest.snapshot_id,
            "tenant_id": manifest.tenant_id,
            "branch_id": manifest.branch_id,
            "node_id": manifest.edge_node_id,
            "source_revision": manifest.source_revision,
        }
        await self.session.execute(
            insert(SyncReadSnapshot).values(
                **scope_values,
                status="draft",
                schema_version=manifest.schema_version,
                credential_kid=manifest.credential_kid,
                manifest_hash=envelope.manifest_hash,
                snapshot_hash=manifest.snapshot_hash,
                signature_algorithm=envelope.signature_algorithm,
                signature=envelope.signature,
                source_generated_at=manifest.source_generated_at,
            )
        )
        for component in manifest.components:
            await self.session.execute(
                insert(SyncReadSnapshotComponent).values(
                    **scope_values,
                    component=component.component,
                    schema_version=component.schema_version,
                    item_count=component.item_count,
                    chunk_count=component.chunk_count,
                    component_hash=component.component_hash,
                )
            )
        for chunk in chunks:
            await self.session.execute(
                insert(SyncReadSnapshotChunk).values(
                    **scope_values,
                    component=chunk.component,
                    chunk_index=chunk.chunk_index,
                    schema_version=chunk.schema_version,
                    item_count=chunk.item_count,
                    payload={"items": list(chunk.payload)},
                    payload_hash=chunk.payload_hash,
                )
            )
        projection_scope = {
            "snapshot_id": manifest.snapshot_id,
            "tenant_id": manifest.tenant_id,
            "branch_id": manifest.branch_id,
            "node_id": manifest.edge_node_id,
        }
        await self.session.execute(
            insert(SyncReadProjection).values(
                **projection_scope,
                source_revision=manifest.source_revision,
                projection_version=1,
                status="building",
                foundation_payload=projection.foundation.model_dump(mode="json"),
                catalog_count=len(projection.catalog),
                supplier_count=len(projection.suppliers),
                inventory_count=len(projection.inventory),
            )
        )
        if projection.catalog:
            await self.session.execute(
                insert(SyncReadCatalogProjection),
                [
                    {
                        **projection_scope,
                        "catalog_id": row.id,
                        "payload": row.model_dump(mode="json"),
                    }
                    for row in projection.catalog
                ],
            )
        barcode_values = [
            {
                **projection_scope,
                "barcode_id": barcode.id,
                "catalog_id": row.id,
                "code": barcode.code,
                "code_type": barcode.code_type,
            }
            for row in projection.catalog
            for barcode in row.barcodes
        ]
        if barcode_values:
            await self.session.execute(insert(SyncReadBarcodeProjection), barcode_values)
        if projection.suppliers:
            await self.session.execute(
                insert(SyncReadSupplierProjection),
                [
                    {
                        **projection_scope,
                        "supplier_id": row.id,
                        "payload": row.model_dump(mode="json"),
                    }
                    for row in projection.suppliers
                ],
            )
        if projection.inventory:
            await self.session.execute(
                insert(SyncReadInventoryProjection),
                [
                    {
                        **projection_scope,
                        "batch_id": row.id,
                        "catalog_id": row.catalog_id,
                        "expires_at": row.expires_at,
                        "qty_remaining": row.qty_remaining,
                        "purchase_price": row.purchase_price,
                        "sale_price": row.sale_price,
                        "is_blocked": row.is_blocked,
                        "payload": row.model_dump(mode="json"),
                    }
                    for row in sorted(projection.inventory, key=lambda item: item.id)
                ],
            )
            has_stock = SyncReadInventoryProjection.qty_remaining > 0
            await self.session.execute(
                insert(SyncReadInventoryRollup).from_select(
                    [
                        "snapshot_id",
                        "tenant_id",
                        "branch_id",
                        "node_id",
                        "has_stock",
                        "expires_at",
                        "batch_count",
                        "total_qty",
                        "purchase_value",
                        "sale_value",
                        "blocked_count",
                    ],
                    select(
                        SyncReadInventoryProjection.snapshot_id,
                        SyncReadInventoryProjection.tenant_id,
                        SyncReadInventoryProjection.branch_id,
                        SyncReadInventoryProjection.node_id,
                        has_stock,
                        SyncReadInventoryProjection.expires_at,
                        func.count(),
                        func.sum(SyncReadInventoryProjection.qty_remaining),
                        func.sum(
                            SyncReadInventoryProjection.qty_remaining
                            * SyncReadInventoryProjection.purchase_price
                        ),
                        func.sum(
                            SyncReadInventoryProjection.qty_remaining
                            * SyncReadInventoryProjection.sale_price
                        ),
                        func.count().filter(SyncReadInventoryProjection.is_blocked.is_(True)),
                    )
                    .where(SyncReadInventoryProjection.snapshot_id == manifest.snapshot_id)
                    .group_by(
                        SyncReadInventoryProjection.snapshot_id,
                        SyncReadInventoryProjection.tenant_id,
                        SyncReadInventoryProjection.branch_id,
                        SyncReadInventoryProjection.node_id,
                        has_stock,
                        SyncReadInventoryProjection.expires_at,
                    ),
                )
            )
        await self.session.execute(
            update(SyncReadProjection)
            .where(SyncReadProjection.snapshot_id == manifest.snapshot_id)
            .values(status="ready")
        )
        await self.session.execute(
            update(SyncReadSnapshot)
            .where(
                SyncReadSnapshot.snapshot_id == manifest.snapshot_id,
                SyncReadSnapshot.tenant_id == manifest.tenant_id,
                SyncReadSnapshot.branch_id == manifest.branch_id,
                SyncReadSnapshot.node_id == manifest.edge_node_id,
            )
            .values(status="ready")
        )
        await self.session.execute(
            text(
                "SELECT public.analyze_ready_sync_read_catalog("
                ":tenant_id, :branch_id, :node_id, :snapshot_id)"
            ),
            projection_scope,
        )
        cursor_insert = pg_insert(SyncReadSnapshotCursor).values(
            tenant_id=manifest.tenant_id,
            branch_id=manifest.branch_id,
            node_id=manifest.edge_node_id,
            active_snapshot_id=manifest.snapshot_id,
            version=1,
            activated_at=func.statement_timestamp(),
            last_confirmed_at=func.statement_timestamp(),
        )
        await self.session.execute(
            cursor_insert.on_conflict_do_update(
                index_elements=[
                    SyncReadSnapshotCursor.tenant_id,
                    SyncReadSnapshotCursor.branch_id,
                    SyncReadSnapshotCursor.node_id,
                ],
                set_={
                    "active_snapshot_id": manifest.snapshot_id,
                    "version": SyncReadSnapshotCursor.version + 1,
                    "activated_at": func.statement_timestamp(),
                    "last_confirmed_at": func.statement_timestamp(),
                    "updated_at": func.statement_timestamp(),
                },
            )
        )

    async def record_quarantine_incident(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        edge_node_id: UUID,
        payload: SyncQuarantineIncidentRequest,
        request_hash: str,
    ) -> SyncQuarantineIncidentRecord:
        return await _record_quarantine_incident(
            self.session,
            tenant_id=tenant_id,
            branch_id=branch_id,
            edge_node_id=edge_node_id,
            payload=payload,
            request_hash=request_hash,
        )

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

    async def get_latest_quarantine_incident(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        edge_node_id: UUID,
        origin_node_id: UUID,
        writer_epoch: int,
    ) -> SyncQuarantineIncident | None:
        result = await self.session.execute(
            select(SyncQuarantineIncident)
            .where(
                SyncQuarantineIncident.tenant_id == tenant_id,
                SyncQuarantineIncident.branch_id == branch_id,
                SyncQuarantineIncident.edge_node_id == edge_node_id,
                SyncQuarantineIncident.origin_node_id == origin_node_id,
                SyncQuarantineIncident.writer_epoch == writer_epoch,
            )
            .order_by(
                SyncQuarantineIncident.received_at.desc(),
                SyncQuarantineIncident.incident_id.desc(),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

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

    async def get_sale_projection_by_operation_id(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
    ) -> SyncSaleProjection | None:
        result = await self.session.execute(
            select(SyncSaleProjection).where(
                SyncSaleProjection.tenant_id == tenant_id,
                SyncSaleProjection.operation_id == operation_id,
            )
        )
        return result.scalar_one_or_none()

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
        sale_type: str,
        parent_sale_id: UUID | None,
        parent_fully_refunded: bool | None,
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
        statement = (
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
                sale_type=sale_type,
                parent_sale_id=parent_sale_id,
                parent_fully_refunded=parent_fully_refunded,
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
        try:
            async with self.session.begin_nested():
                result = await self.session.execute(statement)
        except IntegrityError as exc:
            if _is_projection_operation_collision(exc):
                raise SyncProjectionOperationCollision from exc
            raise
        return result.scalar_one()

    async def list_refund_projections(
        self,
        *,
        parent_sale_id: UUID,
    ) -> list[SyncSaleProjection]:
        result = await self.session.execute(
            select(SyncSaleProjection)
            .where(
                SyncSaleProjection.parent_sale_id == parent_sale_id,
                SyncSaleProjection.sale_type == "return",
            )
            .order_by(SyncSaleProjection.sequence)
        )
        return list(result.scalars().all())

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
