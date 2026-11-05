"""Business rules for enrollment, pull, apply, and shadow verification."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID, uuid5

import structlog
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.core.errors import (
    AurumError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.time import utc_now
from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.retail_refund_events import (
    RetailRefundEventResult,
    retail_refund_event_fingerprint,
    validate_retail_refund_event,
)
from app.domains.pos.retail_sale_event_storage import (
    RetailSaleEventAnchor,
    decode_retail_sale_event,
    encode_retail_sale_event,
)
from app.domains.pos.retail_sale_events import RetailCheckoutResult
from app.domains.pos.schemas import SaleCheckoutResult, SaleRefundResult
from app.domains.sync.activation_bootstrap import (
    ActivationBootstrapValidationError,
    ActivationSnapshotScope,
    build_activation_bootstrap,
    foundation_hash,
    snapshot_hash,
)
from app.domains.sync.bootstrap import (
    BootstrapScope,
    BootstrapValidationError,
    bootstrap_expires_at,
    bootstrap_id_for,
    build_manifest,
    chunk_payload_hash,
)
from app.domains.sync.credentials import EdgeCredential, issue_edge_credential
from app.domains.sync.integrity import (
    canonical_json_bytes,
    canonical_json_hash,
    projection_stream_checksum,
    sale_projection_hash,
    source_stream_checksum,
)
from app.domains.sync.integrity import (
    canonical_json_hash as payload_hash,
)
from app.domains.sync.models import (
    SyncCursor,
    SyncInboxEvent,
    SyncNode,
    SyncOutboxEvent,
    SyncPhysicalStockProjection,
    SyncReadSnapshot,
    SyncReadSnapshotComponent,
    SyncRetailRefundProjection,
    SyncSaleProjection,
    SyncWriterActivation,
)
from app.domains.sync.physical_read_snapshot import PhysicalRecord
from app.domains.sync.physical_stock_events import (
    EVENT_TYPE as PHYSICAL_STOCK_EVENT_TYPE,
)
from app.domains.sync.physical_stock_events import (
    PhysicalStockChange,
    PhysicalStockEventAnchor,
    decode_physical_stock_event,
    encode_physical_stock_event,
)
from app.domains.sync.read_snapshot import (
    REQUIRED_COMPONENTS,
    ChunkDescriptor,
    ChunkRead,
    ComponentDescriptor,
    Manifest,
    ManifestRead,
    ReadSnapshotComponent,
    ReadSnapshotScope,
)
from app.domains.sync.read_snapshot import (
    build_manifest as build_read_snapshot_manifest,
)
from app.domains.sync.read_snapshot_projection import (
    CatalogProductRecord,
    FoundationRecord,
    InventoryBatchRecord,
    SupplierRecord,
    build_component_chunks,
)
from app.domains.sync.refund_read_projection import validate_retail_refund_parent
from app.domains.sync.repository import (
    ActivationFoundationSource,
    SyncCloudRepository,
    SyncEdgeRepository,
    SyncHealth,
    SyncProjectionOperationCollision,
)
from app.domains.sync.schemas import (
    EdgeApplyResult,
    SyncActivationBootstrapRead,
    SyncActivationFoundationSnapshot,
    SyncBootstrapChunkRead,
    SyncBootstrapManifestRead,
    SyncCredentialRotationSecretRead,
    SyncCredentialRotationStartRequest,
    SyncCredentialRotationTransitionRead,
    SyncEventEnvelope,
    SyncMonitoringNodeRead,
    SyncMonitoringRead,
    SyncMonitoringSummaryRead,
    SyncMonitoringTenantRead,
    SyncNodeActionRequest,
    SyncNodeCreate,
    SyncNodeCredentialRead,
    SyncNodeLifecycleRead,
    SyncNodeRead,
    SyncPullResponse,
    SyncQuarantineIncidentRead,
    SyncQuarantineIncidentRequest,
    SyncShadowReportRead,
    SyncShadowReportRequest,
    SyncWriterActivationRead,
    SyncWriterEpochRead,
    SyncWriterPrepareRequest,
    SyncWriterReadinessRead,
    SyncWriterReadinessRequest,
    SyncWriterTransitionRequest,
)

logger = structlog.get_logger("sync.service")
CursorStatus = Literal["synced", "gap", "quarantined", "mismatch"]
ProjectedSale = SaleCheckoutResult | SaleRefundResult | RetailCheckoutResult
_QUARANTINE_INCIDENT_NAMESPACE = UUID("c4d7c709-8bd5-4f89-94cb-fcd324d8800b")


class _EnvelopeError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _require_compatible_delivery(rows: Sequence[SyncOutboxEvent]) -> None:
    """Keep unsupported retail events in order until their receiver is ready."""
    if any(
        row.event_type
        in {
            "pos.sale.completed.v3",
            "pos.sale.completed.v4",
            PHYSICAL_STOCK_EVENT_TYPE,
            "pos.sale.refunded.v6",
        }
        for row in rows
    ):
        raise BusinessRuleError("Retail event delivery requires a compatible receiver")


def _quarantine_evidence_hash(
    *,
    tenant_id: UUID,
    branch_id: UUID,
    edge_node_id: UUID,
    payload: SyncQuarantineIncidentRequest,
) -> str:
    return canonical_json_hash(
        {
            "tenant_id": str(tenant_id),
            "branch_id": str(branch_id),
            "edge_node_id": str(edge_node_id),
            "origin_node_id": str(payload.origin_node_id),
            "writer_epoch": payload.writer_epoch,
            "cursor_status": payload.cursor_status,
            "reason_code": payload.reason_code,
            "last_applied_sequence": payload.last_applied_sequence,
            "blocked_sequence": payload.blocked_sequence,
            "blocked_event_id": (
                str(payload.blocked_event_id) if payload.blocked_event_id is not None else None
            ),
            "blocked_operation_id": (
                str(payload.blocked_operation_id)
                if payload.blocked_operation_id is not None
                else None
            ),
            "source_checksum": payload.source_checksum,
            "projection_checksum": payload.projection_checksum,
            "event_type": payload.event_type,
            "schema_version": payload.schema_version,
        }
    )


def _handover_error(exc: DBAPIError) -> AurumError:
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "42501":
        return PermissionDeniedError("Writer handover operation is not allowed")
    if sqlstate in {"22023", "23502"}:
        return BusinessRuleError("Writer handover request is invalid")
    if sqlstate in {"23503", "23505", "23514", "40001", "40P01", "55000"}:
        return ConflictError("Writer handover state changed; refresh and retry")
    return AurumError("Writer handover database guard failed")


def _node_lifecycle_error(exc: DBAPIError) -> AurumError:
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "42501":
        return PermissionDeniedError("Sync node operation is not allowed")
    if sqlstate == "P0002":
        return NotFoundError("Edge node or credential rotation not found")
    if sqlstate in {"22023", "23502", "23514"}:
        return BusinessRuleError("Sync node operation request is invalid")
    if sqlstate in {"23503", "23505", "40001", "40P01", "55000"}:
        return ConflictError("Sync node state changed; refresh and retry")
    return AurumError("Sync node database guard failed")


def _node_with_credential(node: SyncNode, credential: EdgeCredential) -> SyncNodeCredentialRead:
    node_data = SyncNodeRead.model_validate(node).model_dump()
    return SyncNodeCredentialRead(**node_data, credential=credential.token)


def _foundation_snapshot(
    source: ActivationFoundationSource,
) -> SyncActivationFoundationSnapshot:
    tenant = source.tenant
    settings = source.settings
    branch = source.branch
    register = source.register
    return SyncActivationFoundationSnapshot.model_validate(
        {
            "tenant": {
                "id": tenant.id,
                "name": tenant.name,
                "legal_name": tenant.legal_name,
                "inn_or_tin": tenant.inn_or_tin,
                "registration_number": tenant.registration_number,
                "legal_address": tenant.legal_address,
                "logo_url": tenant.logo_url,
                "status": tenant.status,
                "drug_catalog_mode": tenant.drug_catalog_mode,
                "suspended_at": tenant.suspended_at,
                "archived_at": tenant.archived_at,
                "updated_at": tenant.updated_at,
            },
            "settings": {
                "tenant_id": settings.tenant_id,
                "expiry_thresholds": settings.expiry_thresholds,
                "expired_sale_mode": settings.expired_sale_mode,
                "refund_reason_mode": settings.refund_reason_mode,
                "session_admin_minutes": settings.session_admin_minutes,
                "session_pos_minutes": settings.session_pos_minutes,
                "pin_mode_enabled": settings.pin_mode_enabled,
                "pos_payment_methods": settings.pos_payment_methods,
                "pos_mixed_payment_enabled": settings.pos_mixed_payment_enabled,
                "draft_sale_lifetime_min": settings.draft_sale_lifetime_min,
                "report_timezone": settings.report_timezone,
                "prescription_warning_text": settings.prescription_warning_text,
                "updated_at": settings.updated_at,
            },
            "branch": {
                "id": branch.id,
                "tenant_id": branch.tenant_id,
                "name": branch.name,
                "address": branch.address,
                "branch_type": branch.branch_type,
                "license_number": branch.license_number,
                "license_expires_at": branch.license_expires_at,
                "working_hours": branch.working_hours,
                "receipt_header": branch.receipt_header,
                "is_active": branch.is_active,
                "updated_at": branch.updated_at,
            },
            "register": {
                "id": register.id,
                "tenant_id": register.tenant_id,
                "branch_id": register.branch_id,
                "name": register.name,
                "printer_type": register.printer_type,
                "is_active": register.is_active,
                "updated_at": register.updated_at,
            },
        }
    )


def _activation_snapshot_scope(
    activation: SyncWriterActivation,
) -> ActivationSnapshotScope:
    return ActivationSnapshotScope(
        activation_id=activation.activation_id,
        tenant_id=activation.tenant_id,
        branch_id=activation.branch_id,
        edge_node_id=activation.writer_node_id,
        register_id=activation.allowed_register_id,
        writer_epoch=activation.writer_epoch,
        previous_writer_epoch=activation.previous_writer_epoch,
        previous_terminal_sequence=activation.previous_terminal_sequence,
        previous_terminal_source_checksum=activation.previous_terminal_source_checksum,
        previous_terminal_projection_checksum=(activation.previous_terminal_projection_checksum),
        receipt_baseline_seq=activation.receipt_baseline_seq,
    )


class SyncAdminService:
    def __init__(self, repo: SyncCloudRepository) -> None:
        self.repo = repo

    async def create_node(self, payload: SyncNodeCreate) -> SyncNodeCredentialRead:
        if not await self.repo.branch_exists(
            tenant_id=payload.tenant_id, branch_id=payload.branch_id
        ):
            raise NotFoundError("Branch not found")
        stream = await self.repo.ensure_stream(
            tenant_id=payload.tenant_id, branch_id=payload.branch_id
        )
        credential = issue_edge_credential()
        credential_issued_at = utc_now()
        node = await self.repo.create_edge_node(
            tenant_id=payload.tenant_id,
            branch_id=payload.branch_id,
            display_name=payload.display_name.strip(),
            credential_kid=credential.kid,
            credential_hash=credential.digest,
            credential_issued_at=credential_issued_at,
            credential_expires_at=credential_issued_at
            + timedelta(days=payload.credential_valid_days),
            shadow_start_origin_node_id=stream.writer_node_id,
            shadow_start_writer_epoch=stream.writer_epoch,
            shadow_start_sequence=stream.last_sequence,
            shadow_start_checksum=stream.current_checksum,
            shadow_start_projection_checksum=stream.current_projection_checksum,
        )
        return _node_with_credential(node, credential)

    async def list_nodes(self, *, tenant_id: UUID | None) -> list[SyncNodeRead]:
        nodes = await self.repo.list_edge_nodes(tenant_id=tenant_id)
        return [SyncNodeRead.model_validate(node) for node in nodes]

    async def monitoring_overview(
        self,
        *,
        tenant_id: UUID | None,
        health: SyncHealth | None,
        mode: str | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> SyncMonitoringRead:
        normalized_query = " ".join(query.split()) if query else None
        rows, total = await self.repo.list_monitoring_nodes(
            tenant_id=tenant_id,
            health=health,
            mode=mode,
            query=normalized_query,
            limit=limit,
            offset=offset,
        )
        summary = await self.repo.monitoring_summary(tenant_id=tenant_id)
        tenants = await self.repo.list_monitoring_tenants()
        return SyncMonitoringRead(
            generated_at=utc_now(),
            summary=SyncMonitoringSummaryRead.model_validate(summary, from_attributes=True),
            tenants=[
                SyncMonitoringTenantRead.model_validate(scope, from_attributes=True)
                for scope in tenants
            ],
            items=[
                SyncMonitoringNodeRead.model_validate(row, from_attributes=True) for row in rows
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def rotate_credential(self, *, node_id: UUID, valid_days: int) -> SyncNodeCredentialRead:
        existing = await self.repo.get_edge_node(node_id)
        if existing is None:
            raise NotFoundError("Edge node not found")
        if existing.status != "active":
            raise BusinessRuleError("Revoked Edge node cannot receive a new credential")
        credential = issue_edge_credential()
        credential_issued_at = utc_now()
        node = await self.repo.rotate_edge_credential(
            node_id=node_id,
            credential_kid=credential.kid,
            credential_hash=credential.digest,
            credential_issued_at=credential_issued_at,
            credential_expires_at=credential_issued_at + timedelta(days=valid_days),
        )
        if node is None:
            raise ConflictError("Edge node changed while rotating its credential")
        return _node_with_credential(node, credential)

    async def start_credential_rotation(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        node_id: UUID,
        payload: SyncCredentialRotationStartRequest,
    ) -> SyncCredentialRotationSecretRead:
        credential = issue_edge_credential()
        issued_at = utc_now()
        expires_at = issued_at + timedelta(days=payload.credential_valid_days)
        request_hash = canonical_json_hash(
            {
                "action": "start_credential_rotation",
                "node_id": str(node_id),
                "expected_version": payload.expected_version,
                "operation_id": str(payload.operation_id),
                "confirmation_name": payload.confirmation_name,
                "credential_valid_days": payload.credential_valid_days,
                "reason_code": payload.reason_code.value,
                "reason": payload.reason,
            }
        )
        try:
            rotation = await self.repo.prepare_credential_rotation(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                node_id=node_id,
                expected_version=payload.expected_version,
                operation_id=payload.operation_id,
                credential_kid=credential.kid,
                credential_hash=credential.digest,
                credential_expires_at=expires_at,
                confirmation_name=payload.confirmation_name,
                request_hash=request_hash,
                reason_code=payload.reason_code.value,
                reason=payload.reason,
            )
        except DBAPIError as exc:
            raise _node_lifecycle_error(exc) from exc
        if rotation is None:
            raise ConflictError("Sync node changed; refresh and retry")
        return SyncCredentialRotationSecretRead(
            rotation_id=rotation.rotation_id,
            node_id=rotation.node_id,
            status=cast(
                Literal["pending", "verified", "completed", "cancelled"],
                rotation.rotation_status,
            ),
            node_version=rotation.node_version,
            credential_issued_at=rotation.credential_issued_at,
            credential_expires_at=rotation.credential_expires_at,
            activate_before=rotation.activate_before,
            verified_at=rotation.verified_at,
            credential=credential.token if rotation.applied else None,
            replayed=not rotation.applied,
        )

    async def transition_credential_rotation(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        rotation_id: UUID,
        action: Literal["complete", "cancel"],
        payload: SyncNodeActionRequest,
    ) -> SyncCredentialRotationTransitionRead:
        request_hash = canonical_json_hash(
            {
                "action": action,
                "rotation_id": str(rotation_id),
                "expected_version": payload.expected_version,
                "operation_id": str(payload.operation_id),
                "confirmation_name": payload.confirmation_name,
                "reason_code": payload.reason_code.value,
                "reason": payload.reason,
            }
        )
        try:
            transition = await self.repo.transition_credential_rotation(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                rotation_id=rotation_id,
                expected_version=payload.expected_version,
                operation_id=payload.operation_id,
                action=action,
                confirmation_name=payload.confirmation_name,
                request_hash=request_hash,
                reason_code=payload.reason_code.value,
                reason=payload.reason,
            )
        except DBAPIError as exc:
            raise _node_lifecycle_error(exc) from exc
        if transition is None:
            raise ConflictError("Sync node changed; refresh and retry")
        return SyncCredentialRotationTransitionRead(
            rotation_id=transition.rotation_id,
            node_id=transition.node_id,
            rotation_status=cast(
                Literal["pending", "verified", "completed", "cancelled"],
                transition.rotation_status,
            ),
            node_status=cast(Literal["active", "revoked"], transition.node_status),
            node_version=transition.node_version,
            replayed=not transition.applied,
        )

    async def revoke_node_safely(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        node_id: UUID,
        payload: SyncNodeActionRequest,
    ) -> SyncNodeLifecycleRead:
        request_hash = canonical_json_hash(
            {
                "action": "revoke_node",
                "node_id": str(node_id),
                "expected_version": payload.expected_version,
                "operation_id": str(payload.operation_id),
                "confirmation_name": payload.confirmation_name,
                "reason_code": payload.reason_code.value,
                "reason": payload.reason,
            }
        )
        try:
            revoked = await self.repo.revoke_node_safely(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                node_id=node_id,
                expected_version=payload.expected_version,
                operation_id=payload.operation_id,
                confirmation_name=payload.confirmation_name,
                request_hash=request_hash,
                reason_code=payload.reason_code.value,
                reason=payload.reason,
            )
        except DBAPIError as exc:
            raise _node_lifecycle_error(exc) from exc
        if revoked is None:
            raise ConflictError("Sync node changed; refresh and retry")
        return SyncNodeLifecycleRead(
            node_id=revoked.node_id,
            node_status=cast(Literal["active", "revoked"], revoked.node_status),
            node_version=revoked.node_version,
            replayed=not revoked.applied,
        )

    async def revoke_node(self, node_id: UUID) -> SyncNodeRead:
        existing = await self.repo.get_edge_node(node_id)
        if existing is None:
            raise NotFoundError("Edge node not found")
        node = await self.repo.revoke_edge_node(node_id)
        if node is None:
            raise BusinessRuleError(
                "Prepared or active writer node cannot be revoked; cancel handover first"
            )
        return SyncNodeRead.model_validate(node)

    async def prepare_writer(self, payload: SyncWriterPrepareRequest) -> SyncWriterActivationRead:
        request_hash = canonical_json_hash(payload.model_dump(mode="json"))
        existing = await self.repo.get_writer_activation(payload.activation_id)
        if existing is not None:
            bootstrap = await self.repo.get_activation_bootstrap(payload.activation_id)
            existing_foundation = await self.repo.get_activation_foundation(payload.activation_id)
            try:
                existing_snapshot = SyncActivationFoundationSnapshot.model_validate(
                    existing_foundation.payload if existing_foundation is not None else None
                )
            except PydanticValidationError as exc:
                raise ConflictError("Activation ID was already used for another handover") from exc
            existing_foundation_hash = foundation_hash(existing_snapshot)
            if (
                existing.prepare_request_hash != request_hash
                or bootstrap is None
                or existing_foundation is None
                or bootstrap.tenant_id != existing.tenant_id
                or bootstrap.branch_id != existing.branch_id
                or bootstrap.edge_node_id != existing.writer_node_id
                or bootstrap.register_id != existing.allowed_register_id
                or bootstrap.writer_epoch != existing.writer_epoch
                or bootstrap.capability != existing.capability
                or bootstrap.profile != "foundation_shadow_v1"
                or bootstrap.readiness_eligible
                or bootstrap.foundation_hash != existing_foundation_hash
                or bootstrap.snapshot_hash != existing.bootstrap_snapshot_hash
                or bootstrap.activation_manifest_hash != existing.activation_manifest_hash
                or bootstrap.foundation_hash != existing_foundation.payload_hash
                or existing_foundation.tenant_id != existing.tenant_id
                or existing_foundation.branch_id != existing.branch_id
                or existing_foundation.edge_node_id != existing.writer_node_id
                or existing_foundation.register_id != existing.allowed_register_id
                or existing_foundation.writer_epoch != existing.writer_epoch
                or existing_foundation.schema_version != 1
                or snapshot_hash(
                    scope=_activation_snapshot_scope(existing),
                    foundation_digest=existing_foundation_hash,
                )
                != existing.bootstrap_snapshot_hash
            ):
                raise ConflictError("Activation ID was already used for another handover")
            return SyncWriterActivationRead.model_validate(existing)

        source = await self.repo.get_activation_foundation_source(
            tenant_id=payload.tenant_id,
            branch_id=payload.branch_id,
            register_id=payload.register_id,
        )
        if source is None:
            raise NotFoundError("Activation foundation scope not found")
        if not source.branch.is_active or not source.register.is_active:
            raise BusinessRuleError("Branch and register must be active for writer preparation")
        try:
            foundation_snapshot = _foundation_snapshot(source)
        except PydanticValidationError as exc:
            raise BusinessRuleError("Activation foundation configuration is invalid") from exc
        foundation_digest = foundation_hash(foundation_snapshot)
        try:
            activation = await self.repo.prepare_writer_handover(
                activation_id=payload.activation_id,
                tenant_id=payload.tenant_id,
                branch_id=payload.branch_id,
                edge_node_id=payload.edge_node_id,
                register_id=payload.register_id,
                expected_writer_epoch=payload.expected_writer_epoch,
                expected_sequence=payload.expected_sequence,
                expected_source_checksum=payload.expected_source_checksum,
                expected_projection_checksum=payload.expected_projection_checksum,
                foundation_hash=foundation_digest,
                request_hash=request_hash,
            )
        except DBAPIError as exc:
            raise _handover_error(exc) from exc
        server_snapshot_hash = snapshot_hash(
            scope=_activation_snapshot_scope(activation),
            foundation_digest=foundation_digest,
        )
        if activation.bootstrap_snapshot_hash != server_snapshot_hash:
            raise AurumError("Prepared writer bootstrap is inconsistent")
        await self.repo.persist_activation_foundation(
            activation=activation,
            foundation_payload=foundation_snapshot.model_dump(mode="json", by_alias=True),
            foundation_hash=foundation_digest,
            snapshot_hash=server_snapshot_hash,
        )
        return SyncWriterActivationRead.model_validate(activation)

    async def activate_writer(
        self,
        *,
        activation_id: UUID,
        payload: SyncWriterTransitionRequest,
    ) -> SyncWriterEpochRead:
        if not get_settings().EDGE_WRITER_ACTIVATION_ENABLED:
            raise BusinessRuleError("Edge writer activation is disabled")
        try:
            epoch = await self.repo.activate_writer_handover(
                activation_id=activation_id,
                tenant_id=payload.tenant_id,
                branch_id=payload.branch_id,
                activation_manifest_hash=payload.activation_manifest_hash,
            )
        except DBAPIError as exc:
            raise _handover_error(exc) from exc
        return SyncWriterEpochRead.model_validate(epoch)

    async def cancel_writer(
        self,
        *,
        activation_id: UUID,
        payload: SyncWriterTransitionRequest,
    ) -> SyncWriterActivationRead:
        try:
            activation = await self.repo.cancel_writer_handover(
                activation_id=activation_id,
                tenant_id=payload.tenant_id,
                branch_id=payload.branch_id,
                activation_manifest_hash=payload.activation_manifest_hash,
            )
        except DBAPIError as exc:
            raise _handover_error(exc) from exc
        return SyncWriterActivationRead.model_validate(activation)


class SyncCloudService:
    def __init__(self, repo: SyncCloudRepository) -> None:
        self.repo = repo

    async def _read_snapshot_for_version(
        self,
        *,
        schema_version: int,
        tenant_id: UUID,
        branch_id: UUID,
        edge_node_id: UUID,
        credential_kid: UUID,
    ) -> tuple[SyncReadSnapshot, list[SyncReadSnapshotComponent]] | None:
        if schema_version == 1:
            return await self.repo.get_latest_read_snapshot(
                tenant_id=tenant_id,
                branch_id=branch_id,
                edge_node_id=edge_node_id,
                credential_kid=credential_kid,
            )
        if schema_version != 2:
            raise BusinessRuleError("Unknown read snapshot version")
        return await self.repo.get_latest_read_snapshot(
            tenant_id=tenant_id,
            branch_id=branch_id,
            edge_node_id=edge_node_id,
            credential_kid=credential_kid,
            schema_version=2,
        )

    async def read_snapshot_manifest(
        self,
        *,
        edge_node_id: UUID,
        tenant_id: UUID,
        branch_id: UUID,
        credential_kid: UUID,
        credential_digest: str | None = None,
        schema_version: int = 1,
    ) -> ManifestRead:
        stored = await self._read_snapshot_for_version(
            schema_version=schema_version,
            tenant_id=tenant_id,
            branch_id=branch_id,
            edge_node_id=edge_node_id,
            credential_kid=credential_kid,
        )
        if stored is None and credential_digest is not None:
            await self.publish_read_snapshot(
                edge_node_id=edge_node_id,
                expected_tenant_id=tenant_id,
                expected_branch_id=branch_id,
                expected_credential_kid=credential_kid,
                credential_digest=credential_digest,
                schema_version=schema_version,
            )
            stored = await self._read_snapshot_for_version(
                schema_version=schema_version,
                tenant_id=tenant_id,
                branch_id=branch_id,
                edge_node_id=edge_node_id,
                credential_kid=credential_kid,
            )
        if stored is None:
            raise NotFoundError("Read snapshot not found")
        snapshot, component_rows = stored
        chunk_rows = await self.repo.list_read_snapshot_chunks(
            snapshot_id=snapshot.snapshot_id,
            tenant_id=tenant_id,
            branch_id=branch_id,
            edge_node_id=edge_node_id,
        )
        component_by_name = {row.component: row for row in component_rows}
        components: list[ComponentDescriptor] = []
        for component_name in REQUIRED_COMPONENTS:
            row = component_by_name.get(component_name)
            if row is None:
                raise AurumError("Stored read snapshot is incomplete")
            descriptors = tuple(
                ChunkDescriptor(
                    schema_version=chunk.schema_version,
                    chunk_index=chunk.chunk_index,
                    item_count=chunk.item_count,
                    payload_hash=chunk.payload_hash,
                )
                for chunk in chunk_rows
                if chunk.component == component_name
            )
            components.append(
                ComponentDescriptor(
                    schema_version=row.schema_version,
                    component=cast(ReadSnapshotComponent, row.component),
                    item_count=row.item_count,
                    chunk_count=row.chunk_count,
                    chunks=descriptors,
                    component_hash=row.component_hash,
                )
            )
        manifest = Manifest(
            schema_version=snapshot.schema_version,
            profile="physical_read_only_v2" if snapshot.schema_version == 2 else "read_only_v1",
            snapshot_id=snapshot.snapshot_id,
            edge_node_id=snapshot.node_id,
            tenant_id=snapshot.tenant_id,
            branch_id=snapshot.branch_id,
            source_revision=snapshot.source_revision,
            source_generated_at=snapshot.source_generated_at,
            credential_kid=snapshot.credential_kid,
            components=tuple(components),
            snapshot_hash=snapshot.snapshot_hash,
        )
        return ManifestRead(
            manifest=manifest,
            manifest_hash=snapshot.manifest_hash,
            signature_algorithm=snapshot.signature_algorithm,
            signature=snapshot.signature,
        )

    async def _physical_records_for_version(
        self,
        *,
        schema_version: int,
        tenant_id: UUID,
        branch_id: UUID,
    ) -> tuple[PhysicalRecord, ...]:
        if schema_version == 1:
            return ()
        if schema_version != 2:
            raise BusinessRuleError("Unknown read snapshot version")
        from app.domains.sync.physical_read_snapshot_repository import read_physical_source_facts
        from app.domains.sync.physical_read_snapshot_service import build_physical_source_records

        facts = await read_physical_source_facts(
            self.repo.session, tenant_id=tenant_id, branch_id=branch_id
        )
        return build_physical_source_records(facts, tenant_id=tenant_id, branch_id=branch_id)

    async def publish_read_snapshot(
        self,
        *,
        edge_node_id: UUID,
        expected_tenant_id: UUID,
        expected_branch_id: UUID,
        expected_credential_kid: UUID,
        credential_digest: str,
        schema_version: int = 1,
    ) -> ManifestRead:
        """Publish one internally consistent snapshot for an active Edge node."""

        if schema_version == 2:
            from app.domains.sync.physical_read_snapshot_repository import (
                assert_physical_source_context,
            )

            await assert_physical_source_context(self.repo.session)
            # No pending edits may be discarded. Clean identities must reload
            # through this transaction's MVCC view, including foundation/legacy rows.
            self.repo.session.expire_all()
        await self.repo.lock_read_snapshot_scope(edge_node_id)
        stored = await self._read_snapshot_for_version(
            schema_version=schema_version,
            tenant_id=expected_tenant_id,
            branch_id=expected_branch_id,
            edge_node_id=edge_node_id,
            credential_kid=expected_credential_kid,
        )
        if stored is not None:
            latest, _ = stored
            snapshot_age = (utc_now() - latest.source_generated_at).total_seconds()
            if snapshot_age < get_settings().EDGE_READ_SNAPSHOT_REFRESH_SECONDS:
                return await self.read_snapshot_manifest(
                    edge_node_id=edge_node_id,
                    tenant_id=expected_tenant_id,
                    branch_id=expected_branch_id,
                    credential_kid=expected_credential_kid,
                    schema_version=schema_version,
                )

        foundation_source = await self.repo.read_snapshot_foundation_source(
            tenant_id=expected_tenant_id, branch_id=expected_branch_id
        )
        if foundation_source is None:
            raise BusinessRuleError("Edge snapshot foundation is incomplete")
        tenant, tenant_settings, branch, registers = foundation_source
        catalog_rows, barcode_rows, supplier_rows = await self.repo.read_snapshot_catalog_source(
            tenant_id=expected_tenant_id
        )
        inventory_rows = await self.repo.read_snapshot_inventory_source(
            tenant_id=expected_tenant_id, branch_id=expected_branch_id
        )

        physical = await self._physical_records_for_version(
            schema_version=schema_version,
            tenant_id=expected_tenant_id,
            branch_id=expected_branch_id,
        )

        foundation = FoundationRecord(
            source_fingerprint="0" * 64,
            tenant={
                "id": tenant.id,
                "name": tenant.name,
                "legal_name": tenant.legal_name,
                "inn_or_tin": tenant.inn_or_tin,
                "registration_number": tenant.registration_number,
                "legal_address": tenant.legal_address,
                "status": tenant.status,
                "drug_catalog_mode": tenant.drug_catalog_mode,
                "updated_at": tenant.updated_at,
            },
            settings={
                "expiry_thresholds": tenant_settings.expiry_thresholds,
                "expired_sale_mode": tenant_settings.expired_sale_mode,
                "refund_reason_mode": tenant_settings.refund_reason_mode,
                "pos_payment_methods": tenant_settings.pos_payment_methods,
                "pos_mixed_payment_enabled": tenant_settings.pos_mixed_payment_enabled,
                "draft_sale_lifetime_min": tenant_settings.draft_sale_lifetime_min,
                "report_timezone": tenant_settings.report_timezone,
                "prescription_warning_text": tenant_settings.prescription_warning_text,
                "version": tenant_settings.version,
                "updated_at": tenant_settings.updated_at,
            },
            branch={
                "id": branch.id,
                "tenant_id": branch.tenant_id,
                "name": branch.name,
                "address": branch.address,
                "branch_type": branch.branch_type,
                "license_number": branch.license_number,
                "license_expires_at": branch.license_expires_at,
                "working_hours": branch.working_hours,
                "receipt_header": branch.receipt_header,
                "is_active": branch.is_active,
                "updated_at": branch.updated_at,
            },
            registers=tuple(
                {
                    "id": register.id,
                    "tenant_id": register.tenant_id,
                    "branch_id": register.branch_id,
                    "name": register.name,
                    "printer_type": register.printer_type,
                    "printer_config": register.printer_config,
                    "card_terminal_id": register.card_terminal_id,
                    "qr_terminal_id": register.qr_terminal_id,
                    "is_active": register.is_active,
                    "updated_at": register.updated_at,
                }
                for register in registers
            ),
        )
        catalog = tuple(
            CatalogProductRecord(
                id=item.id,
                tenant_id=item.tenant_id,
                brand_name=item.brand_name,
                inn=item.inn,
                manufacturer=item.manufacturer,
                form=item.form,
                dosage=item.dosage,
                pack_size=item.pack_size,
                atx_code=item.atx_code,
                dispensing_type=item.dispensing_type,
                storage_type=item.storage_type,
                category=item.category,
                base_price=item.base_price,
                currency=item.currency,
                image_version=item.image_version,
                is_active=item.is_active,
                created_at=item.created_at,
                updated_at=item.updated_at,
                deleted_at=item.deleted_at,
                barcodes=tuple(
                    {"id": barcode.id, "code": barcode.code, "code_type": barcode.code_type}
                    for barcode in barcode_rows.get(item.id, ())
                ),
            )
            for item in catalog_rows
        )
        suppliers = tuple(
            SupplierRecord(
                id=item.id,
                tenant_id=item.tenant_id,
                name=item.name,
                legal_name=item.legal_name,
                inn_or_tin=item.inn_or_tin,
                contact_person=item.contact_person,
                phone=item.phone,
                email=item.email,
                address=item.address,
                notes=item.notes,
                is_active=item.is_active,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in supplier_rows
        )
        inventory = tuple(
            InventoryBatchRecord(
                id=batch.id,
                tenant_id=batch.tenant_id,
                branch_id=batch.branch_id,
                catalog_id=batch.catalog_id,
                batch_number=batch.batch_number,
                manufactured_at=batch.manufactured_at,
                expires_at=batch.expires_at,
                purchase_price=batch.purchase_price,
                sale_price=batch.sale_price,
                currency=batch.currency,
                qty_initial=batch.qty_initial,
                qty_remaining=batch.qty_remaining,
                is_blocked=batch.is_blocked,
                block_reason=batch.block_reason,
                blocked_at=batch.blocked_at,
                created_at=batch.created_at,
                updated_at=batch.updated_at,
                branch_name=branch_name,
                catalog_name=product.brand_name,
                catalog_form=product.form,
                catalog_dosage=product.dosage,
                catalog_pack_size=product.pack_size,
            )
            for batch, branch_name, product in inventory_rows
        )
        source_payload: dict[str, object] = {
            "foundation": foundation.model_dump(mode="json", exclude={"source_fingerprint"}),
            "catalog": [item.model_dump(mode="json") for item in catalog],
            "suppliers": [item.model_dump(mode="json") for item in suppliers],
            "inventory": [item.model_dump(mode="json") for item in inventory],
        }
        if schema_version == 2:
            source_payload["physical"] = [item.model_dump(mode="json") for item in physical]
        source_fingerprint = canonical_json_hash(source_payload)
        foundation = foundation.model_copy(update={"source_fingerprint": source_fingerprint})
        if stored is not None:
            latest, _ = stored
            latest_chunks = await self.repo.list_read_snapshot_chunks(
                snapshot_id=latest.snapshot_id,
                tenant_id=expected_tenant_id,
                branch_id=expected_branch_id,
                edge_node_id=edge_node_id,
            )
            prior_fingerprint: object | None = None
            for chunk in latest_chunks:
                if chunk.component != "foundation" or chunk.chunk_index != 0:
                    continue
                stored_items = chunk.payload.get("items")
                if not isinstance(stored_items, list) or not stored_items:
                    continue
                first_item = stored_items[0]
                if isinstance(first_item, dict):
                    prior_fingerprint = first_item.get("source_fingerprint")
                break
            if prior_fingerprint == source_fingerprint:
                return await self.read_snapshot_manifest(
                    edge_node_id=edge_node_id,
                    tenant_id=expected_tenant_id,
                    branch_id=expected_branch_id,
                    credential_kid=expected_credential_kid,
                    schema_version=schema_version,
                )
        revision = await self.repo.next_read_snapshot_revision(
            tenant_id=expected_tenant_id,
            branch_id=expected_branch_id,
            edge_node_id=edge_node_id,
        )
        chunks_by_component = build_component_chunks(
            foundation=foundation,
            catalog=catalog,
            suppliers=suppliers,
            inventory=inventory,
            chunk_size=get_settings().EDGE_READ_SNAPSHOT_CHUNK_SIZE,
            physical=physical,
        )
        envelope, chunks = build_read_snapshot_manifest(
            ReadSnapshotScope(
                edge_node_id=edge_node_id,
                tenant_id=expected_tenant_id,
                branch_id=expected_branch_id,
                source_revision=revision,
            ),
            chunks_by_component,
            credential_kid=expected_credential_kid,
            credential_digest=credential_digest,
            source_generated_at=utc_now(),
            schema_version=cast(Literal[1, 2], schema_version),
        )
        await self.repo.publish_read_snapshot(envelope=envelope, chunks=chunks)
        return envelope

    async def read_snapshot_chunk(
        self,
        *,
        snapshot_id: UUID,
        component: ReadSnapshotComponent,
        chunk_index: int,
        edge_node_id: UUID,
        tenant_id: UUID,
        branch_id: UUID,
        credential_kid: UUID,
        schema_version: int = 1,
    ) -> ChunkRead:
        manifest = await self.read_snapshot_manifest(
            edge_node_id=edge_node_id,
            tenant_id=tenant_id,
            branch_id=branch_id,
            credential_kid=credential_kid,
            schema_version=schema_version,
        )
        if manifest.manifest.snapshot_id != snapshot_id:
            raise NotFoundError("Read snapshot not found")
        row = await self.repo.get_read_snapshot_chunk(
            snapshot_id=snapshot_id,
            tenant_id=tenant_id,
            branch_id=branch_id,
            edge_node_id=edge_node_id,
            component=component,
            chunk_index=chunk_index,
        )
        if row is None:
            raise NotFoundError("Read snapshot chunk not found")
        payload = row.payload.get("items")
        return ChunkRead.model_validate(
            {
                "schema_version": row.schema_version,
                "snapshot_id": row.snapshot_id,
                "edge_node_id": row.node_id,
                "tenant_id": row.tenant_id,
                "branch_id": row.branch_id,
                "source_revision": row.source_revision,
                "component": row.component,
                "chunk_index": row.chunk_index,
                "item_count": row.item_count,
                "payload_hash": row.payload_hash,
                "payload": payload,
            }
        )

    async def read_snapshot_chunk_batch(
        self,
        *,
        snapshot_id: UUID,
        offset: int,
        limit: int,
        edge_node_id: UUID,
        tenant_id: UUID,
        branch_id: UUID,
        credential_kid: UUID,
        schema_version: int = 1,
    ) -> list[ChunkRead]:
        manifest = await self.read_snapshot_manifest(
            edge_node_id=edge_node_id,
            tenant_id=tenant_id,
            branch_id=branch_id,
            credential_kid=credential_kid,
            schema_version=schema_version,
        )
        if manifest.manifest.snapshot_id != snapshot_id:
            raise NotFoundError("Read snapshot not found")
        rows = await self.repo.list_read_snapshot_chunks(
            snapshot_id=snapshot_id,
            tenant_id=tenant_id,
            branch_id=branch_id,
            edge_node_id=edge_node_id,
        )
        return [
            ChunkRead.model_validate(
                {
                    "schema_version": row.schema_version,
                    "snapshot_id": row.snapshot_id,
                    "edge_node_id": row.node_id,
                    "tenant_id": row.tenant_id,
                    "branch_id": row.branch_id,
                    "source_revision": row.source_revision,
                    "component": row.component,
                    "chunk_index": row.chunk_index,
                    "item_count": row.item_count,
                    "payload_hash": row.payload_hash,
                    "payload": row.payload.get("items"),
                }
            )
            for row in rows[offset : offset + limit]
        ]

    async def activation_foundation_bootstrap(
        self,
        *,
        activation_id: UUID,
        edge_node_id: UUID,
        tenant_id: UUID,
        branch_id: UUID,
        credential_kid: UUID,
        credential_digest: str,
        credential_expires_at: datetime,
    ) -> SyncActivationBootstrapRead:
        activation = await self.repo.get_writer_activation(activation_id)
        bootstrap = await self.repo.get_activation_bootstrap(activation_id)
        foundation_row = await self.repo.get_activation_foundation(activation_id)
        if activation is None or bootstrap is None or foundation_row is None:
            raise NotFoundError("Activation foundation bootstrap not found")
        if (
            activation.tenant_id != tenant_id
            or activation.branch_id != branch_id
            or activation.writer_node_id != edge_node_id
            or activation.state not in {"prepared", "ready"}
            or bootstrap.tenant_id != tenant_id
            or bootstrap.branch_id != branch_id
            or bootstrap.edge_node_id != edge_node_id
            or bootstrap.register_id != activation.allowed_register_id
            or bootstrap.writer_epoch != activation.writer_epoch
            or bootstrap.capability != activation.capability
            or bootstrap.profile != "foundation_shadow_v1"
            or bootstrap.readiness_eligible
            or bootstrap.snapshot_hash != activation.bootstrap_snapshot_hash
            or bootstrap.activation_manifest_hash != activation.activation_manifest_hash
            or foundation_row.tenant_id != tenant_id
            or foundation_row.branch_id != branch_id
            or foundation_row.edge_node_id != edge_node_id
            or foundation_row.register_id != activation.allowed_register_id
            or foundation_row.writer_epoch != activation.writer_epoch
            or foundation_row.payload_hash != bootstrap.foundation_hash
        ):
            raise AurumError("Activation foundation bootstrap is inconsistent")
        try:
            foundation = SyncActivationFoundationSnapshot.model_validate(foundation_row.payload)
            return build_activation_bootstrap(
                scope=_activation_snapshot_scope(activation),
                foundation=foundation,
                stored_foundation_hash=bootstrap.foundation_hash,
                stored_snapshot_hash=bootstrap.snapshot_hash,
                activation_manifest_hash=bootstrap.activation_manifest_hash,
                credential_kid=credential_kid,
                credential_digest=credential_digest,
                prepared_at=activation.prepared_at,
                credential_expires_at=credential_expires_at,
                ttl_seconds=get_settings().EDGE_BOOTSTRAP_TTL_SECONDS,
                now=utc_now(),
            )
        except (ActivationBootstrapValidationError, PydanticValidationError) as exc:
            raise AurumError("Activation foundation bootstrap is invalid") from exc

    async def pull(
        self,
        *,
        edge_node_id: UUID,
        tenant_id: UUID,
        branch_id: UUID,
        shadow_start_sequence: int,
        shadow_start_checksum: str,
        shadow_start_projection_checksum: str,
        shadow_start_origin_node_id: UUID | None = None,
        shadow_start_writer_epoch: int | None = None,
        after_sequence: int,
        limit: int,
    ) -> SyncPullResponse:
        stream = await self.repo.get_stream(tenant_id=tenant_id, branch_id=branch_id)
        if stream is None:
            raise AurumError("Sync stream is unavailable")
        if after_sequence == 0:
            effective_after = shadow_start_sequence
        elif after_sequence < shadow_start_sequence:
            raise ConflictError("Sync cursor predates this Edge enrollment")
        else:
            effective_after = after_sequence
        if effective_after > stream.last_sequence:
            raise ConflictError("Sync cursor is ahead of the Cloud stream")
        if effective_after == shadow_start_sequence and (
            (
                shadow_start_origin_node_id is not None
                and shadow_start_origin_node_id != stream.writer_node_id
            )
            or (
                shadow_start_writer_epoch is not None
                and shadow_start_writer_epoch != stream.writer_epoch
            )
        ):
            raise ConflictError("Edge enrollment belongs to a different writer epoch")

        if effective_after == shadow_start_sequence:
            after_source_checksum = shadow_start_checksum
            after_projection_checksum = shadow_start_projection_checksum
        else:
            checkpoint = await self.repo.get_event_at_sequence(
                tenant_id=tenant_id,
                branch_id=branch_id,
                origin_node_id=stream.writer_node_id,
                writer_epoch=stream.writer_epoch,
                sequence=effective_after,
            )
            if (
                checkpoint is None
                or checkpoint.stream_checksum is None
                or checkpoint.projection_checksum is None
            ):
                raise AurumError("Cloud sync checkpoint is incomplete")
            after_source_checksum = checkpoint.stream_checksum
            after_projection_checksum = checkpoint.projection_checksum

        rows = await self.repo.list_events(
            tenant_id=tenant_id,
            branch_id=branch_id,
            origin_node_id=stream.writer_node_id,
            writer_epoch=stream.writer_epoch,
            after_sequence=effective_after,
            limit=limit + 1,
        )
        _require_compatible_delivery(rows)
        has_more = len(rows) > limit
        rows = rows[:limit]
        envelopes: list[SyncEventEnvelope] = []
        expected_sequence = effective_after + 1
        for row in rows:
            if (
                row.origin_node_id != stream.writer_node_id
                or row.writer_epoch != stream.writer_epoch
                or row.sequence != expected_sequence
                or row.stream_checksum is None
                or row.projection_hash is None
                or row.projection_checksum is None
            ):
                raise AurumError("Cloud sync stream is not contiguous")
            envelopes.append(SyncEventEnvelope.model_validate(row))
            expected_sequence += 1

        return SyncPullResponse(
            edge_node_id=edge_node_id,
            tenant_id=tenant_id,
            branch_id=branch_id,
            origin_node_id=stream.writer_node_id,
            writer_epoch=stream.writer_epoch,
            effective_after_sequence=effective_after,
            after_source_checksum=after_source_checksum,
            after_projection_checksum=after_projection_checksum,
            cloud_last_sequence=stream.last_sequence,
            events=envelopes,
            has_more=has_more,
        )

    async def _bootstrap_bundle(
        self,
        *,
        scope: BootstrapScope,
    ) -> tuple[SyncBootstrapManifestRead, list[list[SyncEventEnvelope]]]:
        settings = get_settings()
        if scope.checkpoint_sequence > settings.EDGE_BOOTSTRAP_MAX_EVENTS:
            raise BusinessRuleError("Bootstrap history requires a compact snapshot")
        rows = await self.repo.list_events(
            tenant_id=scope.tenant_id,
            branch_id=scope.branch_id,
            origin_node_id=scope.origin_node_id,
            writer_epoch=scope.writer_epoch,
            after_sequence=0,
            limit=max(1, scope.checkpoint_sequence + 1),
            through_sequence=scope.checkpoint_sequence,
        )
        _require_compatible_delivery(rows)
        if len(rows) != scope.checkpoint_sequence:
            raise AurumError("Bootstrap history is incomplete")
        events = [SyncEventEnvelope.model_validate(row) for row in rows]
        previous_source = scope.root_source_checksum
        previous_projection = scope.root_projection_checksum
        try:
            for event in events:
                SyncEdgeApplyService._validate_event(
                    event,
                    previous_source_checksum=previous_source,
                    previous_projection_checksum=previous_projection,
                )
                previous_source = event.stream_checksum
                previous_projection = event.projection_checksum
            signed, chunks = build_manifest(
                edge_node_id=scope.edge_node_id,
                tenant_id=scope.tenant_id,
                branch_id=scope.branch_id,
                credential_kid=scope.credential_kid,
                credential_digest=scope.credential_digest,
                credential_issued_at=scope.credential_issued_at,
                credential_expires_at=scope.credential_expires_at,
                origin_node_id=scope.origin_node_id,
                writer_epoch=scope.writer_epoch,
                root_source_checksum=scope.root_source_checksum,
                root_projection_checksum=scope.root_projection_checksum,
                checkpoint_sequence=scope.checkpoint_sequence,
                source_checksum=scope.source_checksum,
                projection_checksum=scope.projection_checksum,
                events=events,
                chunk_size=settings.EDGE_BOOTSTRAP_CHUNK_SIZE,
                ttl_seconds=settings.EDGE_BOOTSTRAP_TTL_SECONDS,
            )
        except (_EnvelopeError, BootstrapValidationError) as exc:
            raise AurumError("Bootstrap integrity validation failed") from exc
        if utc_now() >= signed.manifest.expires_at:
            raise BusinessRuleError(
                "Bootstrap enrollment window expired; rotate the Edge credential"
            )
        return signed, chunks

    async def bootstrap_manifest(self, *, scope: BootstrapScope) -> SyncBootstrapManifestRead:
        signed, _ = await self._bootstrap_bundle(scope=scope)
        return signed

    async def bootstrap_chunk(
        self,
        *,
        bootstrap_id: UUID,
        chunk_index: int,
        scope: BootstrapScope,
    ) -> SyncBootstrapChunkRead:
        settings = get_settings()
        if scope.checkpoint_sequence > settings.EDGE_BOOTSTRAP_MAX_EVENTS:
            raise BusinessRuleError("Bootstrap history requires a compact snapshot")
        expected_bootstrap_id = bootstrap_id_for(
            edge_node_id=scope.edge_node_id,
            tenant_id=scope.tenant_id,
            branch_id=scope.branch_id,
            credential_kid=scope.credential_kid,
            origin_node_id=scope.origin_node_id,
            writer_epoch=scope.writer_epoch,
            checkpoint_sequence=scope.checkpoint_sequence,
            issued_at=scope.credential_issued_at,
        )
        if expected_bootstrap_id != bootstrap_id:
            raise NotFoundError("Bootstrap not found")
        chunk_count = (
            scope.checkpoint_sequence + settings.EDGE_BOOTSTRAP_CHUNK_SIZE - 1
        ) // settings.EDGE_BOOTSTRAP_CHUNK_SIZE
        if chunk_index < 0 or chunk_index >= chunk_count:
            raise NotFoundError("Bootstrap chunk not found")
        try:
            expires_at = bootstrap_expires_at(
                credential_issued_at=scope.credential_issued_at,
                credential_expires_at=scope.credential_expires_at,
                ttl_seconds=settings.EDGE_BOOTSTRAP_TTL_SECONDS,
            )
        except BootstrapValidationError as exc:
            raise AurumError("Bootstrap integrity validation failed") from exc
        if utc_now() >= expires_at:
            raise BusinessRuleError(
                "Bootstrap enrollment window expired; rotate the Edge credential"
            )

        first_sequence = chunk_index * settings.EDGE_BOOTSTRAP_CHUNK_SIZE + 1
        last_sequence = min(
            scope.checkpoint_sequence,
            first_sequence + settings.EDGE_BOOTSTRAP_CHUNK_SIZE - 1,
        )
        rows = await self.repo.list_events(
            tenant_id=scope.tenant_id,
            branch_id=scope.branch_id,
            origin_node_id=scope.origin_node_id,
            writer_epoch=scope.writer_epoch,
            after_sequence=first_sequence - 1,
            through_sequence=last_sequence,
            limit=settings.EDGE_BOOTSTRAP_CHUNK_SIZE + 1,
        )
        _require_compatible_delivery(rows)
        if len(rows) != last_sequence - first_sequence + 1:
            raise AurumError("Bootstrap chunk history is incomplete")
        events = [SyncEventEnvelope.model_validate(row) for row in rows]
        if first_sequence == 1:
            previous_source = scope.root_source_checksum
            previous_projection = scope.root_projection_checksum
        else:
            previous = await self.repo.get_event_at_sequence(
                tenant_id=scope.tenant_id,
                branch_id=scope.branch_id,
                origin_node_id=scope.origin_node_id,
                writer_epoch=scope.writer_epoch,
                sequence=first_sequence - 1,
            )
            if (
                previous is None
                or previous.stream_checksum is None
                or previous.projection_checksum is None
            ):
                raise AurumError("Bootstrap chunk checkpoint is incomplete")
            previous_source = previous.stream_checksum
            previous_projection = previous.projection_checksum
        try:
            for expected_sequence, event in enumerate(events, start=first_sequence):
                if event.sequence != expected_sequence:
                    raise BootstrapValidationError("Bootstrap chunk sequence is invalid")
                SyncEdgeApplyService._validate_event(
                    event,
                    previous_source_checksum=previous_source,
                    previous_projection_checksum=previous_projection,
                )
                previous_source = event.stream_checksum
                previous_projection = event.projection_checksum
        except (_EnvelopeError, BootstrapValidationError) as exc:
            raise AurumError("Bootstrap chunk integrity validation failed") from exc
        return SyncBootstrapChunkRead(
            bootstrap_id=bootstrap_id,
            chunk_index=chunk_index,
            payload_hash=chunk_payload_hash(index=chunk_index, events=events),
            events=events,
        )

    async def report(
        self,
        *,
        edge_node_id: UUID,
        tenant_id: UUID,
        branch_id: UUID,
        shadow_start_sequence: int,
        shadow_start_checksum: str,
        shadow_start_projection_checksum: str,
        payload: SyncShadowReportRequest,
    ) -> SyncShadowReportRead:
        request_hash = canonical_json_hash(payload.model_dump(mode="json"))
        existing = await self.repo.get_shadow_report(payload.report_id)
        if existing is not None:
            if existing.request_hash != request_hash or existing.edge_node_id != edge_node_id:
                raise ConflictError("Report ID was already used for another checkpoint")
            return SyncShadowReportRead.model_validate(existing, from_attributes=True)

        stream = await self.repo.get_stream(tenant_id=tenant_id, branch_id=branch_id)
        if stream is None:
            raise AurumError("Sync stream is unavailable")
        if (
            payload.origin_node_id != stream.writer_node_id
            or payload.writer_epoch != stream.writer_epoch
        ):
            raise ConflictError("Shadow report targets a stale writer epoch")
        if not shadow_start_sequence <= payload.last_sequence <= stream.last_sequence:
            raise ConflictError("Shadow report sequence is outside the enrolled stream")

        if payload.last_sequence == shadow_start_sequence:
            expected_source = shadow_start_checksum
            expected_projection = shadow_start_projection_checksum
        else:
            checkpoint = await self.repo.get_event_at_sequence(
                tenant_id=tenant_id,
                branch_id=branch_id,
                origin_node_id=payload.origin_node_id,
                writer_epoch=payload.writer_epoch,
                sequence=payload.last_sequence,
            )
            if (
                checkpoint is None
                or checkpoint.stream_checksum is None
                or checkpoint.projection_checksum is None
            ):
                raise AurumError("Cloud sync checkpoint is incomplete")
            expected_source = checkpoint.stream_checksum
            expected_projection = checkpoint.projection_checksum
        source_verified = payload.source_checksum == expected_source
        report_status = (
            "matched"
            if source_verified and payload.projection_checksum == expected_projection
            else "mismatch"
        )
        report = await self.repo.insert_shadow_report(
            report_id=payload.report_id,
            tenant_id=tenant_id,
            branch_id=branch_id,
            edge_node_id=edge_node_id,
            origin_node_id=payload.origin_node_id,
            writer_epoch=payload.writer_epoch,
            last_sequence=payload.last_sequence,
            source_checksum=payload.source_checksum,
            expected_source_checksum=expected_source,
            source_verified=source_verified,
            projection_checksum=payload.projection_checksum,
            expected_checksum=expected_projection,
            request_hash=request_hash,
            status=report_status,
        )
        if report_status == "mismatch":
            logger.error(
                "edge_shadow_mismatch",
                edge_node_id=str(edge_node_id),
                branch_id=str(branch_id),
                sequence=payload.last_sequence,
            )
        return SyncShadowReportRead.model_validate(report, from_attributes=True)

    async def report_quarantine_incident(
        self,
        *,
        edge_node_id: UUID,
        tenant_id: UUID,
        branch_id: UUID,
        payload: SyncQuarantineIncidentRequest,
    ) -> SyncQuarantineIncidentRead:
        if payload.evidence_hash != _quarantine_evidence_hash(
            tenant_id=tenant_id,
            branch_id=branch_id,
            edge_node_id=edge_node_id,
            payload=payload,
        ):
            raise BusinessRuleError("Edge incident evidence hash is invalid")
        request_hash = canonical_json_hash(payload.model_dump(mode="json"))
        try:
            async with self.repo.session.begin_nested():
                record = await self.repo.record_quarantine_incident(
                    tenant_id=tenant_id,
                    branch_id=branch_id,
                    edge_node_id=edge_node_id,
                    payload=payload,
                    request_hash=request_hash,
                )
        except DBAPIError as exc:
            sqlstate = getattr(exc.orig, "sqlstate", None)
            if sqlstate == "42501":
                raise PermissionDeniedError("Edge incident is not allowed") from exc
            if sqlstate in {"22023", "23514"}:
                raise BusinessRuleError("Edge incident evidence is invalid") from exc
            if sqlstate == "23505":
                raise ConflictError("Incident identity conflicts with existing evidence") from exc
            raise AurumError("Edge incident could not be recorded") from exc
        return SyncQuarantineIncidentRead(
            incident_id=record.incident_id,
            origin_node_id=record.origin_node_id,
            writer_epoch=record.writer_epoch,
            cursor_status=cast(CursorStatus, record.cursor_status),
            reason_code=record.reason_code,
            last_applied_sequence=record.last_applied_sequence,
            blocked_sequence=record.blocked_sequence,
            blocked_event_id=record.blocked_event_id,
            blocked_operation_id=record.blocked_operation_id,
            source_checksum=record.source_checksum,
            projection_checksum=record.projection_checksum,
            event_type=record.event_type,
            schema_version=record.schema_version,
            observed_at=record.observed_at,
            evidence_hash=record.evidence_hash,
            received_at=record.received_at,
            replayed=record.replayed,
        )

    async def record_writer_readiness(
        self,
        *,
        edge_node_id: UUID,
        tenant_id: UUID,
        branch_id: UUID,
        payload: SyncWriterReadinessRequest,
    ) -> SyncWriterReadinessRead:
        settings = get_settings()
        if not settings.EDGE_WRITER_READINESS_ENABLED:
            raise BusinessRuleError("Edge writer readiness is disabled")
        bootstrap = await self.repo.get_activation_bootstrap(payload.activation_id)
        if (
            bootstrap is None
            or bootstrap.edge_node_id != edge_node_id
            or bootstrap.tenant_id != tenant_id
            or bootstrap.branch_id != branch_id
            or bootstrap.profile != "cash_sale_v1_full_v1"
            or not bootstrap.readiness_eligible
        ):
            raise BusinessRuleError("Full Edge activation bootstrap is not available")
        request_hash = canonical_json_hash(payload.model_dump(mode="json"))
        try:
            readiness = await self.repo.record_writer_readiness(
                activation_id=payload.activation_id,
                writer_epoch=payload.writer_epoch,
                previous_sequence=payload.previous_sequence,
                previous_source_checksum=payload.previous_source_checksum,
                previous_projection_checksum=payload.previous_projection_checksum,
                bootstrap_snapshot_hash=payload.bootstrap_snapshot_hash,
                activation_manifest_hash=payload.activation_manifest_hash,
                receipt_baseline_seq=payload.receipt_baseline_seq,
                request_hash=request_hash,
            )
        except DBAPIError as exc:
            raise _handover_error(exc) from exc
        if (
            readiness.edge_node_id != edge_node_id
            or readiness.tenant_id != tenant_id
            or readiness.branch_id != branch_id
        ):
            raise AurumError("Writer readiness scope is inconsistent")
        return SyncWriterReadinessRead.model_validate(readiness)


class SyncEdgeApplyService:
    def __init__(self, repo: SyncEdgeRepository) -> None:
        self.repo = repo
        self._edge_node_id: UUID | None = None

    @staticmethod
    def _result(
        cursor: SyncCursor,
        *,
        applied: int = 0,
        duplicates: int = 0,
        incident: SyncQuarantineIncidentRequest | None = None,
    ) -> EdgeApplyResult:
        return EdgeApplyResult(
            applied=applied,
            duplicates=duplicates,
            last_sequence=cursor.last_sequence,
            source_checksum=cursor.source_checksum,
            projection_checksum=cursor.projection_checksum,
            status=cast(CursorStatus, cursor.status),
            incident=incident,
        )

    @staticmethod
    def _incident_from_record(record: object) -> SyncQuarantineIncidentRequest:
        return SyncQuarantineIncidentRequest.model_validate(record, from_attributes=True)

    async def _stop(
        self,
        *,
        cursor: SyncCursor,
        envelope: SyncEventEnvelope | None,
        cursor_status: str,
        reason_code: str,
        applied: int,
        duplicates: int,
    ) -> EdgeApplyResult:
        if self._edge_node_id is None:
            raise AurumError("Edge node scope is unavailable")
        if envelope is not None:
            inbox = await self.repo.get_inbox_event(envelope.event_id)
            if inbox is None:
                await self.repo.insert_inbox(
                    envelope,
                    status="quarantined",
                    reason_code=reason_code,
                )
            elif inbox.status != "quarantined":
                await self.repo.mark_inbox(
                    inbox,
                    status="quarantined",
                    reason_code=reason_code,
                    applied_at=inbox.applied_at,
                )
        await self.repo.update_cursor(
            cursor,
            last_sequence=cursor.last_sequence,
            last_event_id=cursor.last_event_id,
            source_checksum=cursor.source_checksum,
            projection_checksum=cursor.projection_checksum,
            status=cursor_status,
        )
        payload = SyncQuarantineIncidentRequest(
            incident_id=UUID(int=0),
            origin_node_id=cursor.origin_node_id,
            writer_epoch=cursor.writer_epoch,
            cursor_status=cast(CursorStatus, cursor_status),
            reason_code=reason_code,
            last_applied_sequence=cursor.last_sequence,
            blocked_sequence=envelope.sequence if envelope is not None else None,
            blocked_event_id=envelope.event_id if envelope is not None else None,
            blocked_operation_id=envelope.operation_id if envelope is not None else None,
            source_checksum=cursor.source_checksum,
            projection_checksum=cursor.projection_checksum,
            event_type=envelope.event_type if envelope is not None else None,
            schema_version=envelope.schema_version if envelope is not None else None,
            observed_at=utc_now(),
            evidence_hash="0" * 64,
        )
        evidence_hash = _quarantine_evidence_hash(
            tenant_id=cursor.tenant_id,
            branch_id=cursor.branch_id,
            edge_node_id=self._edge_node_id,
            payload=payload,
        )
        payload = payload.model_copy(
            update={
                "incident_id": uuid5(_QUARANTINE_INCIDENT_NAMESPACE, evidence_hash),
                "evidence_hash": evidence_hash,
            }
        )
        record = await self.repo.record_quarantine_incident(
            tenant_id=cursor.tenant_id,
            branch_id=cursor.branch_id,
            edge_node_id=self._edge_node_id,
            payload=payload,
            request_hash=canonical_json_hash(payload.model_dump(mode="json")),
        )
        return self._result(
            cursor,
            applied=applied,
            duplicates=duplicates,
            incident=self._incident_from_record(record),
        )

    @staticmethod
    def _same_inbox_event(existing: SyncInboxEvent, incoming: SyncEventEnvelope) -> bool:
        same = (
            existing.event_id == incoming.event_id
            and existing.tenant_id == incoming.tenant_id
            and existing.branch_id == incoming.branch_id
            and existing.origin_node_id == incoming.origin_node_id
            and existing.writer_epoch == incoming.writer_epoch
            and existing.sequence == incoming.sequence
            and existing.payload_hash == incoming.payload_hash
            and existing.stream_checksum == incoming.stream_checksum
            and existing.projection_hash == incoming.projection_hash
            and existing.projection_checksum == incoming.projection_checksum
            and existing.status == "applied"
        )
        if existing.schema_version in (3, 4, 6) or incoming.schema_version in (3, 4, 6):
            try:
                same_payload = canonical_json_bytes(existing.payload) == canonical_json_bytes(
                    incoming.payload
                )
            except (ValueError, TypeError, OverflowError):
                return False
            same = same and (
                existing.operation_id == incoming.operation_id
                and existing.aggregate_type == incoming.aggregate_type
                and existing.aggregate_id == incoming.aggregate_id
                and existing.event_type == incoming.event_type
                and existing.schema_version == incoming.schema_version
                and existing.occurred_at == incoming.occurred_at
                and same_payload
            )
        return same

    async def _validate_retail_slot(self, envelope: SyncEventEnvelope) -> None:
        if self._edge_node_id is None:
            raise _EnvelopeError("retail_anchor_scope_unavailable")
        refund = await self.repo.get_retail_refund_anchor(
            tenant_id=envelope.tenant_id,
            branch_id=envelope.branch_id,
            edge_node_id=self._edge_node_id,
            origin_node_id=envelope.origin_node_id,
            writer_epoch=envelope.writer_epoch,
            sequence=envelope.sequence,
        )
        if refund is not None and (
            refund.event_id != envelope.event_id
            or envelope.schema_version != 6
            or envelope.event_type != "pos.sale.refunded.v6"
        ):
            raise _EnvelopeError("retail_refund_anchor_mismatch")
        stock = await self.repo.get_physical_stock_anchor(
            tenant_id=envelope.tenant_id,
            branch_id=envelope.branch_id,
            edge_node_id=self._edge_node_id,
            origin_node_id=envelope.origin_node_id,
            writer_epoch=envelope.writer_epoch,
            sequence=envelope.sequence,
        )
        if stock is not None and (
            stock.event_id != envelope.event_id
            or envelope.schema_version != 5
            or envelope.event_type != PHYSICAL_STOCK_EVENT_TYPE
        ):
            raise _EnvelopeError("physical_stock_anchor_mismatch")
        row = await self.repo.get_retail_event_anchor_by_position(
            tenant_id=envelope.tenant_id,
            branch_id=envelope.branch_id,
            edge_node_id=self._edge_node_id,
            origin_node_id=envelope.origin_node_id,
            writer_epoch=envelope.writer_epoch,
            sequence=envelope.sequence,
        )
        if row is not None and (
            row.event_id != envelope.event_id
            or envelope.schema_version not in (3, 4)
            or envelope.event_type != f"pos.sale.completed.v{envelope.schema_version}"
        ):
            raise _EnvelopeError("retail_anchor_mismatch")

    async def _physical_stock_result(
        self,
        envelope: SyncEventEnvelope,
        *,
        previous_checksums: tuple[str, str] | None = None,
    ) -> PhysicalStockChange:
        if self._edge_node_id is None:
            raise _EnvelopeError("physical_stock_anchor_scope_unavailable")
        row = await self.repo.get_physical_stock_anchor(
            event_id=envelope.event_id,
            tenant_id=envelope.tenant_id,
            branch_id=envelope.branch_id,
            edge_node_id=self._edge_node_id,
        )
        if row is None:
            raise _EnvelopeError("physical_stock_anchor_missing")
        if previous_checksums is not None and (
            row.previous_source_checksum != previous_checksums[0]
            or row.previous_projection_checksum != previous_checksums[1]
        ):
            raise _EnvelopeError("physical_stock_anchor_checkpoint_mismatch")
        try:
            anchor = PhysicalStockEventAnchor(
                tenant_id=row.tenant_id,
                branch_id=row.branch_id,
                origin_node_id=row.origin_node_id,
                writer_epoch=row.writer_epoch,
                sequence=row.sequence,
                previous_source_checksum=row.previous_source_checksum,
                previous_projection_checksum=row.previous_projection_checksum,
                event_fingerprint=row.event_fingerprint,
            )
            return decode_physical_stock_event(
                encode_physical_stock_event(envelope, anchor=anchor), anchor=anchor
            )
        except (ValueError, TypeError, OverflowError) as exc:
            raise _EnvelopeError("physical_stock_anchor_mismatch") from exc

    async def _retail_result(
        self,
        envelope: SyncEventEnvelope,
        *,
        previous_checksums: tuple[str, str] | None = None,
    ) -> RetailCheckoutResult:
        if self._edge_node_id is None:
            raise _EnvelopeError("retail_anchor_scope_unavailable")
        row = await self.repo.get_retail_event_anchor(
            event_id=envelope.event_id,
            tenant_id=envelope.tenant_id,
            branch_id=envelope.branch_id,
            edge_node_id=self._edge_node_id,
        )
        if row is None:
            raise _EnvelopeError("retail_anchor_missing")
        if row.event_id != envelope.event_id or row.target_edge_node_id != self._edge_node_id:
            raise _EnvelopeError("retail_anchor_mismatch")
        if previous_checksums is not None and (
            row.previous_source_checksum != previous_checksums[0]
            or row.previous_projection_checksum != previous_checksums[1]
        ):
            raise _EnvelopeError("retail_anchor_checkpoint_mismatch")
        try:
            anchor = RetailSaleEventAnchor(
                tenant_id=row.tenant_id,
                branch_id=row.branch_id,
                origin_node_id=row.origin_node_id,
                writer_epoch=row.writer_epoch,
                sequence=row.sequence,
                previous_source_checksum=row.previous_source_checksum,
                previous_projection_checksum=row.previous_projection_checksum,
                event_fingerprint=row.event_fingerprint,
            )
            record = encode_retail_sale_event(envelope, anchor=anchor)
            result = decode_retail_sale_event(record, anchor=anchor)
            if result.is_test:
                raise ValueError("training retail events are not enabled")
        except (ValueError, TypeError, OverflowError) as exc:
            raise _EnvelopeError("retail_anchor_mismatch") from exc
        return result

    async def _refund_result(
        self,
        envelope: SyncEventEnvelope,
        *,
        previous_checksums: tuple[str, str] | None = None,
        lock_parent: bool = False,
        validate_parent: bool = True,
    ) -> RetailRefundEventResult:
        if self._edge_node_id is None:
            raise _EnvelopeError("retail_refund_anchor_scope_unavailable")
        row = await self.repo.get_retail_refund_anchor(
            event_id=envelope.event_id,
            tenant_id=envelope.tenant_id,
            branch_id=envelope.branch_id,
            edge_node_id=self._edge_node_id,
        )
        if row is None:
            raise _EnvelopeError("retail_refund_anchor_missing")
        if (
            row.event_id != envelope.event_id
            or row.tenant_id != envelope.tenant_id
            or row.branch_id != envelope.branch_id
            or row.target_edge_node_id != self._edge_node_id
            or (row.origin_node_id, row.writer_epoch, row.sequence)
            != (envelope.origin_node_id, envelope.writer_epoch, envelope.sequence)
        ):
            raise _EnvelopeError("retail_refund_anchor_mismatch")
        checksums = (row.previous_source_checksum, row.previous_projection_checksum)
        if previous_checksums is not None and previous_checksums != checksums:
            raise _EnvelopeError("retail_refund_anchor_checkpoint_mismatch")
        try:
            result = validate_retail_refund_event(
                envelope,
                previous_source_checksum=checksums[0],
                previous_projection_checksum=checksums[1],
            )
            if retail_refund_event_fingerprint(envelope) != row.event_fingerprint:
                raise ValueError("Refund fingerprint differs from independent trust")
        except (ValueError, TypeError, OverflowError) as exc:
            raise _EnvelopeError("retail_refund_anchor_mismatch") from exc
        if validate_parent:
            await self._validate_retail_refund_parent(result, envelope, lock_parent=lock_parent)
        return result

    @staticmethod
    def _assert_refund_projection(
        row: SyncRetailRefundProjection,
        envelope: SyncEventEnvelope,
        result: RetailRefundEventResult,
    ) -> None:
        if (
            row.source_event_id != envelope.event_id
            or row.tenant_id != envelope.tenant_id
            or row.branch_id != envelope.branch_id
            or row.parent_sale_id != result.result.parent_sale_id
            or row.origin_node_id != envelope.origin_node_id
            or row.writer_epoch != envelope.writer_epoch
            or row.sequence != envelope.sequence
            or row.operation_id != envelope.operation_id
            or row.source_id != envelope.aggregate_id
            or row.source_payload_hash != envelope.payload_hash
            or row.projection_hash != envelope.projection_hash
            or canonical_json_bytes(row.payload) != canonical_json_bytes(envelope.payload)
        ):
            raise ValueError("Refund projection differs from its applied source")

    async def _validate_retail_refund_parent(
        self,
        result: RetailRefundEventResult,
        envelope: SyncEventEnvelope,
        *,
        lock_parent: bool,
    ) -> None:
        parent_id = result.result.parent_sale_id
        parent = (
            await self.repo.get_sale_projection_for_update(parent_id)
            if lock_parent
            else await self.repo.get_sale_projection(parent_id)
        )
        if parent is None:
            raise _EnvelopeError("retail_refund_parent_missing")
        if parent.schema_version not in (3, 4) or parent.sale_type != "sale":
            raise _EnvelopeError("retail_refund_parent_version_unsupported")
        source = await self.repo.get_inbox_event(parent.source_event_id)
        if source is None or source.status != "applied":
            raise _EnvelopeError("retail_refund_parent_source_missing")
        try:
            original = SyncEventEnvelope.model_validate(source, from_attributes=True)
            trusted = await self._retail_result(original)
            if (
                parent.origin_node_id != original.origin_node_id
                or parent.writer_epoch != original.writer_epoch
                or parent.sequence != original.sequence
                or parent.source_payload_hash != original.payload_hash
                or parent.projection_hash != original.projection_hash
                or canonical_json_bytes(self._projection_payload(parent))
                != canonical_json_bytes(trusted.model_dump(mode="json"))
                or (parent.writer_epoch, parent.sequence)
                >= (envelope.writer_epoch, envelope.sequence)
            ):
                raise ValueError("Refund parent projection differs from historical source")
            prior = await self._retail_refund_history(envelope, parent_id, lock_parent=lock_parent)
            validate_retail_refund_parent(result, trusted, prior)
        except (ValueError, TypeError, OverflowError) as exc:
            raise _EnvelopeError("retail_refund_parent_mismatch") from exc

    async def _retail_refund_history(
        self, envelope: SyncEventEnvelope, parent_id: UUID, *, lock_parent: bool
    ) -> list[RetailRefundEventResult]:
        rows = await self.repo.list_retail_refund_projections(
            tenant_id=envelope.tenant_id,
            branch_id=envelope.branch_id,
            parent_sale_id=parent_id,
        )
        if len(rows) > 10_000:
            raise ValueError("Refund history bound exceeded")
        current = envelope.writer_epoch, envelope.sequence
        prior: list[RetailRefundEventResult] = []
        for row in rows:
            position = row.writer_epoch, row.sequence
            if position >= current:
                if lock_parent and row.source_event_id != envelope.event_id:
                    raise ValueError("Refund history already contains a later event")
                continue
            inbox = await self.repo.get_inbox_event(row.source_event_id)
            if inbox is None or inbox.status != "applied":
                raise ValueError("Preceding refund has no applied source")
            original = SyncEventEnvelope.model_validate(inbox, from_attributes=True)
            old = await self._refund_result(original, validate_parent=False)
            self._assert_refund_projection(row, original, old)
            prior.append(old)
        return prior

    async def validate_refund_projection(
        self, envelope: SyncEventEnvelope
    ) -> RetailRefundEventResult:
        """Read-only source/parent/prefix validation for the private materializer."""
        if self._edge_node_id is None:
            self._edge_node_id = await self.repo.get_local_edge_node_id()
        await self._validate_retail_slot(envelope)
        result = await self._refund_result(envelope)
        row = await self.repo.get_retail_refund_projection(envelope.event_id)
        inbox = await self.repo.get_inbox_event(envelope.event_id)
        if row is None or inbox is None or inbox.status != "applied":
            raise ValueError("Refund projection has no complete applied source")
        original = SyncEventEnvelope.model_validate(inbox, from_attributes=True)
        if canonical_json_bytes(original.model_dump(mode="json")) != canonical_json_bytes(
            envelope.model_dump(mode="json")
        ):
            raise ValueError("Refund input differs from its applied source")
        self._assert_refund_projection(row, envelope, result)
        return result

    async def _validate_incoming_event(
        self, envelope: SyncEventEnvelope, *, cursor: SyncCursor
    ) -> ProjectedSale:
        if envelope.schema_version not in (3, 4):
            return self._validate_event(
                envelope,
                previous_source_checksum=cursor.source_checksum,
                previous_projection_checksum=cursor.projection_checksum,
            )
        return await self._retail_result(
            envelope,
            previous_checksums=(cursor.source_checksum, cursor.projection_checksum),
        )

    @staticmethod
    def _validate_event(
        envelope: SyncEventEnvelope,
        *,
        previous_source_checksum: str,
        previous_projection_checksum: str,
    ) -> SaleCheckoutResult | SaleRefundResult:
        if envelope.schema_version != 1:
            raise _EnvelopeError("unsupported_event")
        if envelope.aggregate_type != "sale":
            raise _EnvelopeError("invalid_aggregate")
        try:
            calculated_payload_hash = payload_hash(envelope.payload)
        except (TypeError, ValueError) as exc:
            raise _EnvelopeError("invalid_payload_json") from exc
        if calculated_payload_hash != envelope.payload_hash:
            raise _EnvelopeError("payload_hash_mismatch")
        try:
            if envelope.event_type == "pos.sale.completed.v1":
                sale: SaleCheckoutResult | SaleRefundResult = SaleCheckoutResult.model_validate(
                    envelope.payload
                )
            elif envelope.event_type == "pos.sale.refunded.v1":
                sale = SaleRefundResult.model_validate(envelope.payload)
            else:
                raise _EnvelopeError("unsupported_event")
        except PydanticValidationError as exc:
            raise _EnvelopeError("invalid_sale_projection") from exc
        if (
            sale.event_id != envelope.event_id
            or sale.sale_id != envelope.aggregate_id
            or sale.operation_id != envelope.operation_id
            or sale.tenant_id != envelope.tenant_id
            or sale.branch_id != envelope.branch_id
            or sale.completed_at != envelope.occurred_at
        ):
            raise _EnvelopeError("payload_envelope_mismatch")

        calculated_source = source_stream_checksum(
            previous_checksum=previous_source_checksum,
            event_id=envelope.event_id,
            tenant_id=envelope.tenant_id,
            branch_id=envelope.branch_id,
            origin_node_id=envelope.origin_node_id,
            writer_epoch=envelope.writer_epoch,
            sequence=envelope.sequence,
            operation_id=envelope.operation_id,
            aggregate_type=envelope.aggregate_type,
            aggregate_id=envelope.aggregate_id,
            event_type=envelope.event_type,
            schema_version=envelope.schema_version,
            occurred_at=envelope.occurred_at,
            payload_hash=envelope.payload_hash,
        )
        if calculated_source != envelope.stream_checksum:
            raise _EnvelopeError("source_checksum_mismatch")

        normalized_payload = sale.model_dump(mode="json")
        calculated_projection_hash = sale_projection_hash(normalized_payload)
        if calculated_projection_hash != envelope.projection_hash:
            raise _EnvelopeError("projection_hash_mismatch")
        calculated_projection = projection_stream_checksum(
            previous_checksum=previous_projection_checksum,
            origin_node_id=envelope.origin_node_id,
            writer_epoch=envelope.writer_epoch,
            sequence=envelope.sequence,
            sale_id=sale.sale_id,
            projection_hash=calculated_projection_hash,
        )
        if calculated_projection != envelope.projection_checksum:
            raise _EnvelopeError("projection_checksum_mismatch")
        return sale

    async def _validate_refund_parent(
        self,
        refund: SaleRefundResult,
    ) -> None:
        parent = await self.repo.get_sale_projection(refund.parent_sale_id)
        if parent is None:
            raise _EnvelopeError("refund_parent_missing")
        if parent.schema_version not in (None, 1):
            raise _EnvelopeError("refund_parent_version_unsupported")
        if (
            parent.sale_type != "sale"
            or parent.tenant_id != refund.tenant_id
            or parent.branch_id != refund.branch_id
            or parent.completed_at > refund.completed_at
        ):
            raise _EnvelopeError("refund_parent_scope_mismatch")
        if (
            parent.register_id != refund.register_id
            or parent.currency != refund.currency
            or parent.is_test != refund.is_test
        ):
            raise _EnvelopeError("refund_parent_scope_mismatch")

        try:
            parent_sale = SaleCheckoutResult.model_validate(self._projection_payload(parent))
            prior_refunds = [
                SaleRefundResult.model_validate(self._projection_payload(row))
                for row in await self.repo.list_refund_projections(
                    parent_sale_id=refund.parent_sale_id
                )
            ]
        except (PydanticValidationError, ValueError) as exc:
            raise _EnvelopeError("refund_history_invalid") from exc

        parent_items = {item.id: item for item in parent_sale.items}
        refunded_qty: dict[UUID, Decimal] = {}
        for prior in prior_refunds:
            for item in prior.items:
                refunded_qty[item.parent_sale_item_id] = (
                    refunded_qty.get(item.parent_sale_item_id, Decimal("0")) + item.qty
                )

        for item in refund.items:
            parent_item = parent_items.get(item.parent_sale_item_id)
            if parent_item is None:
                raise _EnvelopeError("refund_item_parent_missing")
            if (
                item.catalog_id != parent_item.catalog_id
                or item.batch_id != parent_item.batch_id
                or item.unit_price != parent_item.unit_price
                or item.currency != parent_item.currency
            ):
                raise _EnvelopeError("refund_item_parent_mismatch")
            new_total = refunded_qty.get(item.parent_sale_item_id, Decimal("0")) + item.qty
            if new_total > parent_item.qty:
                raise _EnvelopeError("refund_quantity_exceeded")
            refunded_qty[item.parent_sale_item_id] = new_total

        fully_refunded = all(
            refunded_qty.get(item.id, Decimal("0")) == item.qty for item in parent_sale.items
        )
        if refund.parent_fully_refunded != fully_refunded:
            raise _EnvelopeError("refund_lifecycle_mismatch")

    async def _prepare_cursor(
        self, pull: SyncPullResponse
    ) -> tuple[SyncCursor, EdgeApplyResult | None]:
        cursor = await self.repo.get_cursor(
            tenant_id=pull.tenant_id,
            branch_id=pull.branch_id,
            origin_node_id=pull.origin_node_id,
            writer_epoch=pull.writer_epoch,
            for_update=True,
        )
        if cursor is None:
            cursor = await self.repo.insert_cursor(
                tenant_id=pull.tenant_id,
                branch_id=pull.branch_id,
                origin_node_id=pull.origin_node_id,
                writer_epoch=pull.writer_epoch,
                start_sequence=pull.effective_after_sequence,
                start_source_checksum=pull.after_source_checksum,
                start_projection_checksum=pull.after_projection_checksum,
            )
        if cursor.status != "synced":
            if self._edge_node_id is None:
                raise AurumError("Edge node scope is unavailable")
            incident = await self.repo.get_latest_quarantine_incident(
                tenant_id=pull.tenant_id,
                branch_id=pull.branch_id,
                edge_node_id=self._edge_node_id,
                origin_node_id=pull.origin_node_id,
                writer_epoch=pull.writer_epoch,
            )
            if incident is None:
                halted = await self._stop(
                    cursor=cursor,
                    envelope=None,
                    cursor_status=cursor.status,
                    reason_code="legacy_unknown_quarantine",
                    applied=0,
                    duplicates=0,
                )
                return cursor, halted
            return cursor, self._result(
                cursor,
                incident=self._incident_from_record(incident),
            )
        if cursor.writer_epoch != pull.writer_epoch:
            halted = await self._stop(
                cursor=cursor,
                envelope=None,
                cursor_status="quarantined",
                reason_code="writer_epoch_mismatch",
                applied=0,
                duplicates=0,
            )
            return cursor, halted
        if pull.effective_after_sequence > cursor.last_sequence:
            halted = await self._stop(
                cursor=cursor,
                envelope=None,
                cursor_status="gap",
                reason_code="response_checkpoint_gap",
                applied=0,
                duplicates=0,
            )
            return cursor, halted
        if pull.effective_after_sequence == cursor.last_sequence and (
            pull.after_source_checksum != cursor.source_checksum
            or pull.after_projection_checksum != cursor.projection_checksum
        ):
            halted = await self._stop(
                cursor=cursor,
                envelope=None,
                cursor_status="mismatch",
                reason_code="response_checkpoint_mismatch",
                applied=0,
                duplicates=0,
            )
            return cursor, halted
        return cursor, None

    async def _validate_duplicate_event(self, envelope: SyncEventEnvelope) -> None:
        if envelope.schema_version in (3, 4):
            await self._retail_result(envelope)
        elif envelope.schema_version == 5:
            await self._physical_stock_result(envelope)
        elif envelope.schema_version == 6:
            try:
                await self.validate_refund_projection(envelope)
            except _EnvelopeError:
                raise
            except (ValueError, TypeError, OverflowError) as exc:
                raise _EnvelopeError("retail_refund_projection_mismatch") from exc

    async def _precheck_event(
        self,
        *,
        pull: SyncPullResponse,
        cursor: SyncCursor,
        envelope: SyncEventEnvelope,
        applied: int,
        duplicates: int,
    ) -> tuple[EdgeApplyResult | None, bool]:
        scope_reason: str | None = None
        if (
            envelope.tenant_id != pull.tenant_id
            or envelope.branch_id != pull.branch_id
            or envelope.origin_node_id != pull.origin_node_id
            or envelope.writer_epoch != pull.writer_epoch
        ):
            scope_reason = "event_scope_mismatch"
        else:
            try:
                await self._validate_retail_slot(envelope)
            except _EnvelopeError as exc:
                scope_reason = exc.reason_code
        if scope_reason is not None:
            halted = await self._stop(
                cursor=cursor,
                envelope=None,
                cursor_status="quarantined",
                reason_code=scope_reason,
                applied=applied,
                duplicates=duplicates,
            )
            return halted, False

        existing = await self.repo.get_inbox_event(envelope.event_id)
        if envelope.sequence <= cursor.last_sequence:
            reason_code = "event_identity_collision"
            if existing is not None and self._same_inbox_event(existing, envelope):
                try:
                    await self._validate_duplicate_event(envelope)
                except _EnvelopeError as exc:
                    reason_code = exc.reason_code
                else:
                    return None, True
            halted = await self._stop(
                cursor=cursor,
                envelope=envelope if existing is None else None,
                cursor_status="quarantined",
                reason_code=reason_code,
                applied=applied,
                duplicates=duplicates,
            )
            return halted, False
        if envelope.sequence != cursor.last_sequence + 1:
            halted = await self._stop(
                cursor=cursor,
                envelope=envelope,
                cursor_status="gap",
                reason_code="sequence_gap",
                applied=applied,
                duplicates=duplicates,
            )
            return halted, False
        if existing is not None:
            halted = await self._stop(
                cursor=cursor,
                envelope=None,
                cursor_status="quarantined",
                reason_code="event_identity_collision",
                applied=applied,
                duplicates=duplicates,
            )
            return halted, False
        return None, False

    async def _apply_new_event(
        self,
        *,
        cursor: SyncCursor,
        envelope: SyncEventEnvelope,
        applied: int,
        duplicates: int,
    ) -> EdgeApplyResult | None:
        if envelope.schema_version == 6:
            return await self._apply_refund_event(
                cursor=cursor, envelope=envelope, applied=applied, duplicates=duplicates
            )
        if envelope.schema_version == 5:
            return await self._apply_stock_event(
                cursor=cursor, envelope=envelope, applied=applied, duplicates=duplicates
            )
        return await self._apply_new_sale_event(
            cursor=cursor, envelope=envelope, applied=applied, duplicates=duplicates
        )

    async def _apply_refund_event(
        self,
        *,
        cursor: SyncCursor,
        envelope: SyncEventEnvelope,
        applied: int,
        duplicates: int,
    ) -> EdgeApplyResult | None:
        try:
            async with self.repo.event_savepoint():
                refund = await self._refund_result(
                    envelope,
                    previous_checksums=(cursor.source_checksum, cursor.projection_checksum),
                    lock_parent=True,
                )
                if (
                    await self.repo.get_retail_refund_projection(envelope.event_id) is not None
                    or await self.repo.get_retail_refund_projection_by_source(
                        tenant_id=envelope.tenant_id, source_id=envelope.aggregate_id
                    )
                    is not None
                    or await self.repo.get_sale_projection(envelope.aggregate_id) is not None
                    or await self.repo.get_sale_projection_by_operation_id(
                        tenant_id=envelope.tenant_id, operation_id=envelope.operation_id
                    )
                    is not None
                ):
                    raise _EnvelopeError("retail_refund_projection_collision")
                inbox = await self.repo.insert_inbox(envelope, status="received")
                await self.repo.insert_retail_refund_projection(
                    envelope, parent_sale_id=refund.result.parent_sale_id
                )
                await self.repo.mark_inbox(
                    inbox, status="applied", reason_code=None, applied_at=utc_now()
                )
                await self.repo.update_cursor(
                    cursor,
                    last_sequence=envelope.sequence,
                    last_event_id=envelope.event_id,
                    source_checksum=envelope.stream_checksum,
                    projection_checksum=envelope.projection_checksum,
                    status="synced",
                )
        except SyncProjectionOperationCollision:
            restored = await self.repo.get_cursor(
                tenant_id=envelope.tenant_id,
                branch_id=envelope.branch_id,
                origin_node_id=envelope.origin_node_id,
                writer_epoch=envelope.writer_epoch,
                for_update=True,
            )
            if restored is None:
                raise AurumError("Edge refund cursor disappeared during recovery") from None
            return await self._stop(
                cursor=restored,
                envelope=envelope,
                cursor_status="quarantined",
                reason_code="operation_id_collision",
                applied=applied,
                duplicates=duplicates,
            )
        except _EnvelopeError as exc:
            return await self._stop(
                cursor=cursor,
                envelope=envelope,
                cursor_status="quarantined",
                reason_code=exc.reason_code,
                applied=applied,
                duplicates=duplicates,
            )
        return None

    async def _apply_stock_event(
        self,
        *,
        cursor: SyncCursor,
        envelope: SyncEventEnvelope,
        applied: int,
        duplicates: int,
    ) -> EdgeApplyResult | None:
        try:
            await self._physical_stock_result(
                envelope, previous_checksums=(cursor.source_checksum, cursor.projection_checksum)
            )
        except _EnvelopeError as exc:
            return await self._stop(
                cursor=cursor,
                envelope=envelope,
                cursor_status="quarantined",
                reason_code=exc.reason_code,
                applied=applied,
                duplicates=duplicates,
            )
        if await self.repo.get_physical_stock_projection(envelope.event_id) is not None:
            return await self._stop(
                cursor=cursor,
                envelope=envelope,
                cursor_status="quarantined",
                reason_code="physical_stock_projection_collision",
                applied=applied,
                duplicates=duplicates,
            )
        async with self.repo.event_savepoint():
            inbox = await self.repo.insert_inbox(envelope, status="received")
            await self.repo.insert_physical_stock_projection(envelope)
            await self.repo.mark_inbox(
                inbox, status="applied", reason_code=None, applied_at=utc_now()
            )
            await self.repo.update_cursor(
                cursor,
                last_sequence=envelope.sequence,
                last_event_id=envelope.event_id,
                source_checksum=envelope.stream_checksum,
                projection_checksum=envelope.projection_checksum,
                status="synced",
            )
        return None

    async def _apply_new_sale_event(
        self,
        *,
        cursor: SyncCursor,
        envelope: SyncEventEnvelope,
        applied: int,
        duplicates: int,
    ) -> EdgeApplyResult | None:
        try:
            sale = await self._validate_incoming_event(envelope, cursor=cursor)
        except _EnvelopeError as exc:
            return await self._stop(
                cursor=cursor,
                envelope=envelope,
                cursor_status="quarantined",
                reason_code=exc.reason_code,
                applied=applied,
                duplicates=duplicates,
            )
        if await self.repo.get_sale_projection(sale.sale_id) is not None:
            return await self._stop(
                cursor=cursor,
                envelope=envelope,
                cursor_status="quarantined",
                reason_code="sale_projection_collision",
                applied=applied,
                duplicates=duplicates,
            )
        if (
            await self.repo.get_sale_projection_by_operation_id(
                tenant_id=sale.tenant_id,
                operation_id=sale.operation_id,
            )
            is not None
        ):
            return await self._stop(
                cursor=cursor,
                envelope=envelope,
                cursor_status="quarantined",
                reason_code="operation_id_collision",
                applied=applied,
                duplicates=duplicates,
            )
        if isinstance(sale, SaleRefundResult):
            try:
                await self._validate_refund_parent(sale)
            except _EnvelopeError as exc:
                return await self._stop(
                    cursor=cursor,
                    envelope=envelope,
                    cursor_status="quarantined",
                    reason_code=exc.reason_code,
                    applied=applied,
                    duplicates=duplicates,
                )

        try:
            if isinstance(sale, PhysicalCheckoutResultV3 | MixedCheckoutResultV4):
                async with self.repo.event_savepoint():
                    await self._persist_new_event(cursor=cursor, envelope=envelope, sale=sale)
            else:
                await self._persist_new_event(cursor=cursor, envelope=envelope, sale=sale)
        except SyncProjectionOperationCollision:
            if isinstance(sale, PhysicalCheckoutResultV3 | MixedCheckoutResultV4):
                restored = await self.repo.get_cursor(
                    tenant_id=envelope.tenant_id,
                    branch_id=envelope.branch_id,
                    origin_node_id=envelope.origin_node_id,
                    writer_epoch=envelope.writer_epoch,
                    for_update=True,
                )
                if restored is None:
                    raise AurumError("Edge sync cursor disappeared during recovery") from None
                cursor = restored
            return await self._stop(
                cursor=cursor,
                envelope=envelope,
                cursor_status="quarantined",
                reason_code="operation_id_collision",
                applied=applied,
                duplicates=duplicates,
            )
        return None

    async def _persist_new_event(
        self, *, cursor: SyncCursor, envelope: SyncEventEnvelope, sale: ProjectedSale
    ) -> None:
        inbox = await self.repo.insert_inbox(envelope, status="received")
        await self.repo.insert_sale_projection(
            sale_id=sale.sale_id,
            tenant_id=sale.tenant_id,
            branch_id=sale.branch_id,
            origin_node_id=envelope.origin_node_id,
            writer_epoch=envelope.writer_epoch,
            sequence=envelope.sequence,
            source_event_id=envelope.event_id,
            operation_id=sale.operation_id,
            sale_type=("return" if isinstance(sale, SaleRefundResult) else "sale"),
            parent_sale_id=(sale.parent_sale_id if isinstance(sale, SaleRefundResult) else None),
            parent_fully_refunded=(
                sale.parent_fully_refunded if isinstance(sale, SaleRefundResult) else None
            ),
            register_id=sale.register_id,
            shift_id=sale.shift_id,
            cashier_user_id=(
                sale.cashier_id
                if isinstance(sale, PhysicalCheckoutResultV3 | MixedCheckoutResultV4)
                else sale.cashier_user_id
            ),
            receipt_number=sale.receipt_number,
            receipt_seq=(
                sale.receipt_sequence
                if isinstance(sale, PhysicalCheckoutResultV3 | MixedCheckoutResultV4)
                else sale.receipt_seq
            ),
            sale_created_at=sale.created_at,
            completed_at=sale.completed_at,
            total_amount=sale.total_amount,
            currency=sale.currency,
            is_test=sale.is_test,
            items=[
                cast(dict[str, object], item.model_dump(mode="json"))
                for item in (
                    sale.lines
                    if isinstance(sale, PhysicalCheckoutResultV3 | MixedCheckoutResultV4)
                    else sale.items
                )
            ],
            payments=[
                cast(dict[str, object], payment.model_dump(mode="json"))
                for payment in sale.payments
            ],
            source_payload_hash=envelope.payload_hash,
            projection_hash=envelope.projection_hash,
            schema_version=(
                sale.schema_version
                if isinstance(sale, PhysicalCheckoutResultV3 | MixedCheckoutResultV4)
                else 1
            ),
            stock_command_id=(
                sale.stock_command_id
                if isinstance(sale, PhysicalCheckoutResultV3 | MixedCheckoutResultV4)
                else None
            ),
        )
        await self.repo.mark_inbox(
            inbox,
            status="applied",
            reason_code=None,
            applied_at=utc_now(),
        )
        await self.repo.update_cursor(
            cursor,
            last_sequence=envelope.sequence,
            last_event_id=envelope.event_id,
            source_checksum=envelope.stream_checksum,
            projection_checksum=envelope.projection_checksum,
            status="synced",
        )

    async def _lock_refund_parents(self, pull: SyncPullResponse, cursor: SyncCursor) -> None:
        """Serialize branch refund history across separately locked epoch cursors."""
        parents: set[UUID] = set()
        scope = pull.tenant_id, pull.branch_id, pull.origin_node_id, pull.writer_epoch
        for envelope in pull.events:
            if (
                envelope.schema_version != 6
                or envelope.sequence <= cursor.last_sequence
                or (
                    envelope.tenant_id,
                    envelope.branch_id,
                    envelope.origin_node_id,
                    envelope.writer_epoch,
                )
                != scope
            ):
                continue
            try:
                result = await self._refund_result(envelope, validate_parent=False)
            except _EnvelopeError:
                # Preserve ordered valid prefixes and the normal quarantine path.
                continue
            parents.add(result.result.parent_sale_id)
        if parents:
            await self.repo.lock_retail_refund_parents(
                tenant_id=pull.tenant_id,
                branch_id=pull.branch_id,
                parent_sale_ids=tuple(sorted(parents)),
            )

    async def apply(self, pull: SyncPullResponse) -> EdgeApplyResult:
        self._edge_node_id = pull.edge_node_id
        cursor, halted = await self._prepare_cursor(pull)
        if halted is not None:
            return halted

        await self._lock_refund_parents(pull, cursor)

        applied = 0
        duplicates = 0
        for envelope in pull.events:
            halted, duplicate = await self._precheck_event(
                pull=pull,
                cursor=cursor,
                envelope=envelope,
                applied=applied,
                duplicates=duplicates,
            )
            if halted is not None:
                return halted
            if duplicate:
                duplicates += 1
                continue
            halted = await self._apply_new_event(
                cursor=cursor,
                envelope=envelope,
                applied=applied,
                duplicates=duplicates,
            )
            if halted is not None:
                return halted
            applied += 1
        return self._result(cursor, applied=applied, duplicates=duplicates)

    @staticmethod
    def _projection_payload(row: SyncSaleProjection) -> dict[str, object]:
        payload: dict[str, object] = {
            "event_id": row.source_event_id,
            "sale_id": row.sale_id,
            "operation_id": row.operation_id,
            "tenant_id": row.tenant_id,
            "branch_id": row.branch_id,
            "register_id": row.register_id,
            "shift_id": row.shift_id,
            "cashier_user_id": row.cashier_user_id,
            "receipt_number": row.receipt_number,
            "receipt_seq": row.receipt_seq,
            "created_at": row.sale_created_at,
            "completed_at": row.completed_at,
            "total_amount": row.total_amount,
            "currency": row.currency,
            "is_test": row.is_test,
            "items": row.items,
            "payments": row.payments,
        }
        if row.schema_version in (3, 4):
            if row.sale_type != "sale" or row.is_test:
                raise ValueError("unsupported retail projection lifecycle")
            del payload["cashier_user_id"], payload["receipt_seq"], payload["items"]
            payload.update(
                {
                    "schema_version": row.schema_version,
                    "document_kind": (
                        "physical_checkout" if row.schema_version == 3 else "mixed_checkout"
                    ),
                    "stock_command_id": row.stock_command_id,
                    "cashier_id": row.cashier_user_id,
                    "receipt_sequence": row.receipt_seq,
                    "lines": row.items,
                }
            )
            retail_sale: RetailCheckoutResult
            if row.schema_version == 3:
                retail_sale = PhysicalCheckoutResultV3.model_validate(payload)
            else:
                retail_sale = MixedCheckoutResultV4.model_validate(payload)
            return cast(dict[str, object], retail_sale.model_dump(mode="json"))
        if row.schema_version not in (None, 1) or row.stock_command_id is not None:
            raise ValueError("unsupported sale projection version")
        if row.sale_type == "return":
            payload.update(
                {
                    "parent_sale_id": row.parent_sale_id,
                    "parent_fully_refunded": row.parent_fully_refunded,
                }
            )
            sale: SaleCheckoutResult | SaleRefundResult = SaleRefundResult.model_validate(payload)
        elif row.sale_type == "sale":
            sale = SaleCheckoutResult.model_validate(payload)
        else:
            raise ValueError("unsupported sale projection type")
        return cast(dict[str, object], sale.model_dump(mode="json"))

    async def _verified_projection_checksums(
        self,
        row: SyncSaleProjection,
        *,
        previous_source_checksum: str,
        previous_projection_checksum: str,
    ) -> tuple[str, str]:
        payload = self._projection_payload(row)
        inbox = await self.repo.get_inbox_event(row.source_event_id)
        if inbox is None or inbox.status != "applied":
            raise ValueError("projection has no applied source")
        envelope = SyncEventEnvelope.model_validate(inbox, from_attributes=True)
        await self._validate_retail_slot(envelope)
        version = row.schema_version if row.schema_version is not None else 1
        if (
            version != envelope.schema_version
            or row.source_payload_hash != envelope.payload_hash
            or row.origin_node_id != envelope.origin_node_id
            or row.writer_epoch != envelope.writer_epoch
            or row.sequence != envelope.sequence
        ):
            raise ValueError("projection metadata differs from its saved source")
        source: ProjectedSale
        if envelope.schema_version in (3, 4):
            source = await self._retail_result(
                envelope,
                previous_checksums=(previous_source_checksum, previous_projection_checksum),
            )
        else:
            source = self._validate_event(
                envelope,
                previous_source_checksum=previous_source_checksum,
                previous_projection_checksum=previous_projection_checksum,
            )
        if canonical_json_bytes(source.model_dump(mode="json")) != canonical_json_bytes(payload):
            raise ValueError("projection differs from its saved source")
        return sale_projection_hash(payload), envelope.stream_checksum

    async def _verified_refund_projection_checksums(
        self,
        row: SyncRetailRefundProjection,
        *,
        previous_source_checksum: str,
        previous_projection_checksum: str,
    ) -> tuple[str, str]:
        inbox = await self.repo.get_inbox_event(row.source_event_id)
        if inbox is None or inbox.status != "applied":
            raise ValueError("Refund projection has no applied source")
        envelope = SyncEventEnvelope.model_validate(inbox, from_attributes=True)
        await self._validate_retail_slot(envelope)
        result = await self._refund_result(
            envelope, previous_checksums=(previous_source_checksum, previous_projection_checksum)
        )
        self._assert_refund_projection(row, envelope, result)
        return canonical_json_hash(row.payload), envelope.stream_checksum

    async def _verified_stock_projection_checksums(
        self,
        row: SyncPhysicalStockProjection,
        *,
        previous_source_checksum: str,
        previous_projection_checksum: str,
    ) -> tuple[str, str]:
        inbox = await self.repo.get_inbox_event(row.source_event_id)
        if inbox is None or inbox.status != "applied":
            raise ValueError("Physical projection has no applied source")
        envelope = SyncEventEnvelope.model_validate(inbox, from_attributes=True)
        await self._validate_retail_slot(envelope)
        change = await self._physical_stock_result(
            envelope, previous_checksums=(previous_source_checksum, previous_projection_checksum)
        )
        if (
            row.tenant_id != envelope.tenant_id
            or row.branch_id != envelope.branch_id
            or row.origin_node_id != envelope.origin_node_id
            or row.writer_epoch != envelope.writer_epoch
            or row.sequence != envelope.sequence
            or row.operation_id != change.operation_id
            or row.source_id != change.source_id
            or row.source_payload_hash != envelope.payload_hash
            or canonical_json_bytes(row.payload)
            != canonical_json_bytes(change.model_dump(mode="json"))
        ):
            raise ValueError("Physical projection differs from its source")
        return canonical_json_hash(row.payload), envelope.stream_checksum

    async def _verified_read_projection_checksums(
        self,
        row: SyncSaleProjection | SyncPhysicalStockProjection | SyncRetailRefundProjection,
        *,
        previous_source_checksum: str,
        previous_projection_checksum: str,
    ) -> tuple[str, str, UUID]:
        if isinstance(row, SyncRetailRefundProjection):
            digest, source = await self._verified_refund_projection_checksums(
                row,
                previous_source_checksum=previous_source_checksum,
                previous_projection_checksum=previous_projection_checksum,
            )
            return digest, source, row.source_id
        if isinstance(row, SyncPhysicalStockProjection):
            digest, source = await self._verified_stock_projection_checksums(
                row,
                previous_source_checksum=previous_source_checksum,
                previous_projection_checksum=previous_projection_checksum,
            )
            return digest, source, row.source_id
        digest, source = await self._verified_projection_checksums(
            row,
            previous_source_checksum=previous_source_checksum,
            previous_projection_checksum=previous_projection_checksum,
        )
        return digest, source, row.sale_id

    async def verify_projection(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        origin_node_id: UUID,
        writer_epoch: int,
    ) -> EdgeApplyResult:
        if self._edge_node_id is None:
            self._edge_node_id = await self.repo.get_local_edge_node_id()
        if self._edge_node_id is None:
            raise PermissionDeniedError("Edge node scope is required for projection verification")
        cursor = await self.repo.get_cursor(
            tenant_id=tenant_id,
            branch_id=branch_id,
            origin_node_id=origin_node_id,
            writer_epoch=writer_epoch,
            for_update=True,
        )
        if cursor is None:
            raise NotFoundError("Edge sync cursor not found")
        sale_rows = await self.repo.list_sale_projections(
            tenant_id=tenant_id,
            branch_id=branch_id,
            origin_node_id=origin_node_id,
            writer_epoch=writer_epoch,
            after_sequence=cursor.start_sequence,
        )
        stock_rows = await self.repo.list_physical_stock_projections(
            tenant_id=tenant_id,
            branch_id=branch_id,
            origin_node_id=origin_node_id,
            writer_epoch=writer_epoch,
            after_sequence=cursor.start_sequence,
        )
        refund_rows = await self.repo.list_retail_refund_projections(
            tenant_id=tenant_id,
            branch_id=branch_id,
            origin_node_id=origin_node_id,
            writer_epoch=writer_epoch,
            after_sequence=cursor.start_sequence,
        )
        if len(refund_rows) > 10_000:
            raise ValueError("Refund projection history bound exceeded")
        rows: list[
            SyncSaleProjection | SyncPhysicalStockProjection | SyncRetailRefundProjection
        ] = sorted([*sale_rows, *stock_rows, *refund_rows], key=lambda row: row.sequence)
        expected_sequence = cursor.start_sequence + 1
        checksum = cursor.start_projection_checksum
        source_checksum = cursor.start_source_checksum
        valid = True
        for row in rows:
            try:
                projection_hash, next_source_checksum, aggregate_id = (
                    await self._verified_read_projection_checksums(
                        row,
                        previous_source_checksum=source_checksum,
                        previous_projection_checksum=checksum,
                    )
                )
            except (ValueError, TypeError, OverflowError):
                valid = False
                break
            if row.sequence != expected_sequence or row.projection_hash != projection_hash:
                valid = False
                break
            checksum = projection_stream_checksum(
                previous_checksum=checksum,
                origin_node_id=row.origin_node_id,
                writer_epoch=row.writer_epoch,
                sequence=row.sequence,
                sale_id=aggregate_id,
                projection_hash=projection_hash,
            )
            source_checksum = next_source_checksum
            expected_sequence += 1
        if (
            expected_sequence - 1 != cursor.last_sequence
            or checksum != cursor.projection_checksum
            or source_checksum != cursor.source_checksum
        ):
            valid = False
        if not valid:
            return await self._stop(
                cursor=cursor,
                envelope=None,
                cursor_status="mismatch",
                reason_code="local_projection_mismatch",
                applied=0,
                duplicates=0,
            )
        return self._result(cursor)
