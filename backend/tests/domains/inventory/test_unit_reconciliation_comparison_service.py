"""Fail closed on source states that immutable database guards currently prevent."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.domains.inventory.unit_legacy_reconciliation import LegacyReconciliationSource
from app.domains.inventory.unit_legacy_reconciliation_record_models import (
    LegacyReconciliationRecord,
)
from app.domains.inventory.unit_legacy_reconciliation_record_repository import (
    LegacyReconciliationRecordRepository,
)
from app.domains.inventory.unit_legacy_reconciliation_record_service import (
    LegacyReconciliationRecordService,
)
from app.domains.inventory.unit_legacy_reconciliation_repository import LegacyReconciliationBatch


class ComparisonRepository(LegacyReconciliationRecordRepository):
    """Only the two read operations exist; any accidental write fails the test."""

    def __init__(
        self, record: LegacyReconciliationRecord, current: LegacyReconciliationBatch | None
    ) -> None:
        self.record = record
        self.current = current

    async def read_record(
        self, *, tenant_id: UUID, record_id: UUID
    ) -> LegacyReconciliationRecord | None:
        assert (tenant_id, record_id) == (self.record.tenant_id, self.record.id)
        return self.record

    async def read_batch(
        self, *, tenant_id: UUID, branch_id: UUID, batch_id: UUID, lock: bool = False
    ) -> LegacyReconciliationBatch | None:
        assert not lock
        assert (tenant_id, branch_id, batch_id) == (
            self.record.tenant_id,
            self.record.branch_id,
            self.record.batch_id,
        )
        return self.current


@pytest.mark.parametrize(
    "state,issue",
    [
        ("missing", "source_missing"),
        ("tenant_id", "source_identity_changed"),
        ("branch_id", "source_identity_changed"),
        ("batch_id", "source_identity_changed"),
        ("catalog_id", "source_identity_changed"),
        ("preparation", "source_mode_changed"),
        ("physical", "source_mode_changed"),
    ],
)
async def test_incompatible_current_source_returns_saved_record_without_conversion(
    state: str, issue: str
) -> None:
    record = LegacyReconciliationRecord(
        tenant_id=uuid4(),
        id=uuid4(),
        branch_id=uuid4(),
        batch_id=uuid4(),
        catalog_id=uuid4(),
        legacy_quantity=Decimal("1.000"),
        count={
            "packaging": {
                "version": 2,
                "unit": "tablet",
                "units_per_pack": 30,
                "units_per_blister": None,
                "dispense_step": 1,
            },
            "closed_packs": 1,
            "opened_packs": [],
        },
        created_at=datetime(2026, 10, 15, tzinfo=UTC),
        created_by=uuid4(),
    )
    source = LegacyReconciliationSource(
        tenant_id=record.tenant_id,
        branch_id=record.branch_id,
        batch_id=record.batch_id,
        catalog_id=record.catalog_id,
        legacy_quantity=record.legacy_quantity,
    )
    if state.endswith("_id"):
        source = source.model_copy(update={state: uuid4()})
    current = (
        None
        if state == "missing"
        else LegacyReconciliationBatch(
            source=source,
            has_preparation=state == "preparation",
            has_physical_snapshot=state == "physical",
        )
    )
    service = LegacyReconciliationRecordService(ComparisonRepository(record, current))
    comparison = await service.compare(
        tenant_id=record.tenant_id,
        branch_id=record.branch_id,
        batch_id=record.batch_id,
        record_id=record.id,
        view_branch_ids={record.branch_id},
    )
    assert comparison.issues == (issue,)
    assert comparison.current_comparison is None
    assert comparison.conversion_authorized is False
    assert comparison.record.id == record.id
    assert comparison.record.preview.source.legacy_quantity == Decimal("1.000")
    assert comparison.record.preview.observed_base_units == 30
