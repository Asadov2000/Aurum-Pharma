"""Persistence for immutable count records; no legacy or physical stock writes."""

from uuid import UUID

from sqlalchemy import select, text

from app.domains.inventory.unit_legacy_reconciliation import (
    LegacyReconciliationCount,
    LegacyReconciliationSource,
)
from app.domains.inventory.unit_legacy_reconciliation_record_models import (
    LegacyReconciliationRecord,
)
from app.domains.inventory.unit_legacy_reconciliation_repository import (
    LegacyReconciliationPreviewRepository,
)


class LegacyReconciliationRecordRepository(LegacyReconciliationPreviewRepository):
    async def lock_command(self, *, tenant_id: UUID, record_id: UUID) -> None:
        await self.session.execute(
            text("SELECT pg_catalog.pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"aurum-legacy-reconciliation-key:{tenant_id}:{record_id}"},
        )

    async def is_record_actor_allowed(
        self, *, tenant_id: UUID, actor_id: UUID, branch_id: UUID
    ) -> bool:
        authorized = await self.session.scalar(
            text(
                "SELECT current_user='aurum_app' AND session_user='aurum_app' "
                "AND NOT public.is_support_session() "
                "AND NOT public.is_tenant_support_session() "
                "AND public.current_tenant_id()=:tenant "
                "AND public.current_app_user_id()=:actor "
                "AND EXISTS(SELECT 1 FROM public.app_user WHERE id=:actor AND status='active') "
                "AND public.tenant_actor_has_scoped_permission(:tenant,'batches.view',:branch) "
                "AND public.tenant_actor_has_scoped_permission(:tenant,'batches.update',:branch) "
                "AND EXISTS(SELECT 1 FROM public.tenant t "
                "JOIN public.tenant_access_policy p ON p.tenant_id=t.id "
                "WHERE t.id=:tenant AND t.status NOT IN ('readonly','suspended','archived') "
                "AND (p.expires_at<=statement_timestamp() "
                "OR (p.mode<>'readonly' AND NOT ('inventory'=ANY(p.blocked_modules)))))"
            ),
            {"tenant": tenant_id, "actor": actor_id, "branch": branch_id},
        )
        return authorized is True

    async def read_record(
        self, *, tenant_id: UUID, record_id: UUID
    ) -> LegacyReconciliationRecord | None:
        result = await self.session.execute(
            select(LegacyReconciliationRecord).where(
                LegacyReconciliationRecord.tenant_id == tenant_id,
                LegacyReconciliationRecord.id == record_id,
            )
        )
        return result.scalar_one_or_none()

    async def insert_record(
        self,
        *,
        record_id: UUID,
        actor_id: UUID,
        source: LegacyReconciliationSource,
        count: LegacyReconciliationCount,
    ) -> LegacyReconciliationRecord:
        record = LegacyReconciliationRecord(
            id=record_id,
            tenant_id=source.tenant_id,
            branch_id=source.branch_id,
            batch_id=source.batch_id,
            catalog_id=source.catalog_id,
            legacy_quantity=source.legacy_quantity,
            count=count.model_dump(mode="json"),
            created_by=actor_id,
        )
        async with self.session.begin_nested():
            self.session.add(record)
            await self.session.flush()
        return record
