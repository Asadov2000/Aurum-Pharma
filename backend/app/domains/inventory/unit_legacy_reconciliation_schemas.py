"""Public count input; legacy quantity and source identity come only from the DB."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.domains.inventory.unit_legacy_reconciliation import (
    ExactUnits,
    LegacyReconciliationCount,
    LegacyReconciliationPreview,
    ReconciliationContract,
)


class LegacyReconciliationPreviewRequest(LegacyReconciliationCount):
    branch_id: UUID


class LegacyReconciliationSaveRequest(LegacyReconciliationPreviewRequest):
    reconciliation_id: UUID
    expected_legacy_quantity: ExactUnits = Field(ge=0, le=Decimal("99999999999.999"))


class LegacyReconciliationRecordResponse(ReconciliationContract):
    id: UUID
    created_at: datetime
    created_by: UUID
    preview: LegacyReconciliationPreview


LegacyReconciliationIssue = Literal[
    "source_missing",
    "source_identity_changed",
    "source_mode_changed",
    "quantity_changed",
    "quantity_not_representable",
    "count_discrepancy",
]


class LegacyReconciliationComparisonResponse(ReconciliationContract):
    """Current quantity comparison, never authorization to transfer stock."""

    record: LegacyReconciliationRecordResponse
    current_comparison: LegacyReconciliationPreview | None
    issues: tuple[LegacyReconciliationIssue, ...]
    conversion_authorized: Literal[False] = False
