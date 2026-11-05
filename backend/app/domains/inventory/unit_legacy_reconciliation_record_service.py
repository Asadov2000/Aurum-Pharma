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
    LegacyReconciliationComparisonResponse,
    LegacyReconciliationIssue,
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

    async def compare(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        batch_id: UUID,
        record_id: UUID,
        view_branch_ids: set[UUID] | None,
    ) -> LegacyReconciliationComparisonResponse:
        """Read a point-in-time comparison without reserving or approving stock.

        Equal quantities cannot prove that no intervening movements occurred.
        A future conversion must independently recheck facts under its locks.
        """
        record = await self.read(
            tenant_id=tenant_id,
            branch_id=branch_id,
            batch_id=batch_id,
            record_id=record_id,
            view_branch_ids=view_branch_ids,
        )
        batch = await self.repository.read_batch(
            tenant_id=tenant_id, branch_id=branch_id, batch_id=batch_id
        )
        source_issue: LegacyReconciliationIssue | None = None
        if batch is None:
            source_issue = "source_missing"
        elif batch.source.model_dump(exclude={"legacy_quantity"}) != (
            record.preview.source.model_dump(exclude={"legacy_quantity"})
        ):
            source_issue = "source_identity_changed"
        elif batch.has_preparation or batch.has_physical_snapshot:
            source_issue = "source_mode_changed"
        if source_issue is not None or batch is None:
            return LegacyReconciliationComparisonResponse(
                record=record,
                current_comparison=None,
                issues=(source_issue or "source_missing",),
            )
        current = preview_legacy_reconciliation(
            batch.source,
            record.preview.observation,
            tenant_id=tenant_id,
            branch_id=branch_id,
        )
        issues: list[LegacyReconciliationIssue] = []
        if batch.source.legacy_quantity != record.preview.source.legacy_quantity:
            issues.append("quantity_changed")
        if not current.recorded_quantity_representable:
            issues.append("quantity_not_representable")
        if current.comparison != "matched":
            issues.append("count_discrepancy")
        return LegacyReconciliationComparisonResponse(
            record=record, current_comparison=current, issues=tuple(issues)
        )

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
        # Waiting for another request may outlive the HTTP authorization snapshot.
        # Replay must recheck actual database permissions, not just the INSERT guard.
        if not await self.repository.is_record_actor_allowed(
            tenant_id=tenant_id, actor_id=actor_id, branch_id=request.branch_id
        ):
            raise PermissionDeniedError("Batch reconciliation permission denied")
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
