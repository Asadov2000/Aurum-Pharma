"""Save observations without approving conversion or changing stock quantities."""

from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.domains.inventory.unit_legacy_reconciliation import (
    LegacyReconciliationCount,
    LegacyReconciliationObservation,
    LegacyReconciliationSource,
    preview_legacy_reconciliation,
)
from app.domains.inventory.unit_legacy_reconciliation_record_models import (
    LegacyReconciliationRecord,
)
from app.domains.inventory.unit_legacy_reconciliation_record_repository import (
    LegacyReconciliationRecordRepository,
)
from app.domains.inventory.unit_legacy_reconciliation_schemas import (
    LegacyReconciliationRecordResponse,
    LegacyReconciliationSaveRequest,
)


def _response(record: LegacyReconciliationRecord) -> LegacyReconciliationRecordResponse:
    source = LegacyReconciliationSource(
        tenant_id=record.tenant_id,
        branch_id=record.branch_id,
        batch_id=record.batch_id,
        catalog_id=record.catalog_id,
        legacy_quantity=record.legacy_quantity,
    )
    count = LegacyReconciliationCount.model_validate(record.count)
    observation = LegacyReconciliationObservation(
        **count.model_dump(),
        tenant_id=source.tenant_id,
        branch_id=source.branch_id,
        batch_id=source.batch_id,
        catalog_id=source.catalog_id,
    )
    return LegacyReconciliationRecordResponse(
        id=record.id,
        created_at=record.created_at,
        created_by=record.created_by,
        preview=preview_legacy_reconciliation(
            source, observation, tenant_id=source.tenant_id, branch_id=source.branch_id
        ),
    )


def _check_branch(branch_id: UUID, allowed_branch_ids: set[UUID] | None) -> None:
    if allowed_branch_ids is not None and branch_id not in allowed_branch_ids:
        raise PermissionDeniedError("Batch reconciliation permission denied")


class LegacyReconciliationRecordService:
    def __init__(self, repository: LegacyReconciliationRecordRepository) -> None:
        self.repository = repository

    async def save(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        batch_id: UUID,
        request: LegacyReconciliationSaveRequest,
        view_branch_ids: set[UUID] | None,
        update_branch_ids: set[UUID] | None,
    ) -> LegacyReconciliationRecordResponse:
        request = LegacyReconciliationSaveRequest.model_validate(request.model_dump())
        _check_branch(request.branch_id, view_branch_ids)
        _check_branch(request.branch_id, update_branch_ids)
        count = LegacyReconciliationCount.model_validate(
            request.model_dump(
                exclude={"branch_id", "reconciliation_id", "expected_legacy_quantity"}
            )
        )
        await self.repository.lock_command(tenant_id=tenant_id, record_id=request.reconciliation_id)
        existing = await self.repository.read_record(
            tenant_id=tenant_id, record_id=request.reconciliation_id
        )
        if existing is not None:
            if (
                existing.tenant_id != tenant_id
                or existing.branch_id != request.branch_id
                or existing.batch_id != batch_id
                or existing.created_by != actor_id
                or existing.legacy_quantity != request.expected_legacy_quantity
                or LegacyReconciliationCount.model_validate(existing.count) != count
            ):
                raise ConflictError("Reconciliation identifier is already used for another count")
            # Replay restores the original observation even if live stock changed.
            return _response(existing)
        batch = await self.repository.read_batch(
            tenant_id=tenant_id, branch_id=request.branch_id, batch_id=batch_id, lock=True
        )
        if batch is None or (
            batch.source.tenant_id,
            batch.source.branch_id,
            batch.source.batch_id,
        ) != (tenant_id, request.branch_id, batch_id):
            raise NotFoundError("Legacy batch not found")
        if batch.has_preparation or batch.has_physical_snapshot:
            raise ConflictError("Unit-prepared stock cannot be recorded as legacy stock")
        if batch.source.legacy_quantity != request.expected_legacy_quantity:
            raise ConflictError("Legacy stock changed; repeat the reconciliation preview")
        try:
            record = await self.repository.insert_record(
                record_id=request.reconciliation_id,
                actor_id=actor_id,
                source=batch.source,
                count=count,
            )
        except IntegrityError as error:
            cause = getattr(error.orig, "__cause__", None)
            constraint = getattr(error.orig, "constraint_name", None) or getattr(
                cause, "constraint_name", None
            )
            if (
                getattr(error.orig, "sqlstate", None) == "23505"
                and constraint == "unit_legacy_reconciliation_record_pkey"
            ):
                # RLS may hide an existing identifier from another branch.
                raise ConflictError("Reconciliation identifier is already used") from error
            raise
        return _response(record)

    async def read(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        batch_id: UUID,
        record_id: UUID,
        view_branch_ids: set[UUID] | None,
    ) -> LegacyReconciliationRecordResponse:
        _check_branch(branch_id, view_branch_ids)
        record = await self.repository.read_record(tenant_id=tenant_id, record_id=record_id)
        if record is None or (record.tenant_id, record.branch_id, record.batch_id) != (
            tenant_id,
            branch_id,
            batch_id,
        ):
            raise NotFoundError("Reconciliation record not found")
        return _response(record)
