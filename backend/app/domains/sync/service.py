"""Business rules for enrollment, pull, apply, and shadow verification."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, cast
from uuid import UUID

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
from app.domains.pos.schemas import SaleCheckoutResult
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
    SyncSaleProjection,
    SyncWriterActivation,
)
from app.domains.sync.repository import (
    ActivationFoundationSource,
    SyncCloudRepository,
    SyncEdgeRepository,
    SyncHealth,
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


class _EnvelopeError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


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

    @staticmethod
    def _result(cursor: SyncCursor, *, applied: int = 0, duplicates: int = 0) -> EdgeApplyResult:
        return EdgeApplyResult(
            applied=applied,
            duplicates=duplicates,
            last_sequence=cursor.last_sequence,
            source_checksum=cursor.source_checksum,
            projection_checksum=cursor.projection_checksum,
            status=cast(CursorStatus, cursor.status),
        )

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
        return self._result(cursor, applied=applied, duplicates=duplicates)

    @staticmethod
    def _same_inbox_event(existing: SyncInboxEvent, incoming: SyncEventEnvelope) -> bool:
        return (
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

    @staticmethod
    def _validate_event(
        envelope: SyncEventEnvelope,
        *,
        previous_source_checksum: str,
        previous_projection_checksum: str,
    ) -> SaleCheckoutResult:
        if envelope.event_type != "pos.sale.completed.v1" or envelope.schema_version != 1:
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
            sale = SaleCheckoutResult.model_validate(envelope.payload)
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
            return cursor, self._result(cursor)
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

    async def _precheck_event(
        self,
        *,
        pull: SyncPullResponse,
        cursor: SyncCursor,
        envelope: SyncEventEnvelope,
        applied: int,
        duplicates: int,
    ) -> tuple[EdgeApplyResult | None, bool]:
        if (
            envelope.tenant_id != pull.tenant_id
            or envelope.branch_id != pull.branch_id
            or envelope.origin_node_id != pull.origin_node_id
            or envelope.writer_epoch != pull.writer_epoch
        ):
            halted = await self._stop(
                cursor=cursor,
                envelope=None,
                cursor_status="quarantined",
                reason_code="event_scope_mismatch",
                applied=applied,
                duplicates=duplicates,
            )
            return halted, False

        existing = await self.repo.get_inbox_event(envelope.event_id)
        if envelope.sequence <= cursor.last_sequence:
            if existing is not None and self._same_inbox_event(existing, envelope):
                return None, True
            halted = await self._stop(
                cursor=cursor,
                envelope=envelope if existing is None else None,
                cursor_status="quarantined",
                reason_code="event_identity_collision",
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
        try:
            sale = self._validate_event(
                envelope,
                previous_source_checksum=cursor.source_checksum,
                previous_projection_checksum=cursor.projection_checksum,
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
        if await self.repo.get_sale_projection(sale.sale_id) is not None:
            return await self._stop(
                cursor=cursor,
                envelope=envelope,
                cursor_status="quarantined",
                reason_code="sale_projection_collision",
                applied=applied,
                duplicates=duplicates,
            )

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
            register_id=sale.register_id,
            shift_id=sale.shift_id,
            cashier_user_id=sale.cashier_user_id,
            receipt_number=sale.receipt_number,
            receipt_seq=sale.receipt_seq,
            sale_created_at=sale.created_at,
            completed_at=sale.completed_at,
            total_amount=sale.total_amount,
            currency=sale.currency,
            is_test=sale.is_test,
            items=[cast(dict[str, object], item.model_dump(mode="json")) for item in sale.items],
            payments=[
                cast(dict[str, object], payment.model_dump(mode="json"))
                for payment in sale.payments
            ],
            source_payload_hash=envelope.payload_hash,
            projection_hash=envelope.projection_hash,
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
        return None

    async def apply(self, pull: SyncPullResponse) -> EdgeApplyResult:
        cursor, halted = await self._prepare_cursor(pull)
        if halted is not None:
            return halted

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
        sale = SaleCheckoutResult.model_validate(
            {
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
        )
        return cast(dict[str, object], sale.model_dump(mode="json"))

    async def verify_projection(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        origin_node_id: UUID,
        writer_epoch: int,
    ) -> EdgeApplyResult:
        cursor = await self.repo.get_cursor(
            tenant_id=tenant_id,
            branch_id=branch_id,
            origin_node_id=origin_node_id,
            writer_epoch=writer_epoch,
            for_update=True,
        )
        if cursor is None:
            raise NotFoundError("Edge sync cursor not found")
        rows = await self.repo.list_sale_projections(
            tenant_id=tenant_id,
            branch_id=branch_id,
            origin_node_id=origin_node_id,
            writer_epoch=writer_epoch,
            after_sequence=cursor.start_sequence,
        )
        expected_sequence = cursor.start_sequence + 1
        checksum = cursor.start_projection_checksum
        valid = True
        for row in rows:
            projection_hash = sale_projection_hash(self._projection_payload(row))
            if row.sequence != expected_sequence or row.projection_hash != projection_hash:
                valid = False
                break
            checksum = projection_stream_checksum(
                previous_checksum=checksum,
                origin_node_id=row.origin_node_id,
                writer_epoch=row.writer_epoch,
                sequence=row.sequence,
                sale_id=row.sale_id,
                projection_hash=projection_hash,
            )
            expected_sequence += 1
        if expected_sequence - 1 != cursor.last_sequence or checksum != cursor.projection_checksum:
            valid = False
        if not valid:
            await self.repo.update_cursor(
                cursor,
                last_sequence=cursor.last_sequence,
                last_event_id=cursor.last_event_id,
                source_checksum=cursor.source_checksum,
                projection_checksum=cursor.projection_checksum,
                status="mismatch",
            )
        return self._result(cursor)
