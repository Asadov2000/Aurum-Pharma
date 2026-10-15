"""Public count input; legacy quantity and source identity come only from the DB."""

from uuid import UUID

from app.domains.inventory.unit_legacy_reconciliation import LegacyReconciliationCount


class LegacyReconciliationPreviewRequest(LegacyReconciliationCount):
    branch_id: UUID
