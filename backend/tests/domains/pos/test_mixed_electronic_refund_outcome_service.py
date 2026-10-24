"""Evidence replay and corruption checks without a terminal or database."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AurumError, BusinessRuleError, ConflictError
from app.domains.pos.mixed_electronic_refund_activation_schemas import (
    SavedMixedElectronicRefundActivation,
)
from app.domains.pos.mixed_electronic_refund_outcome_models import MixedElectronicRefundOutcome
from app.domains.pos.mixed_electronic_refund_outcome_repository import (
    MixedElectronicRefundOutcomeRepository,
)
from app.domains.pos.mixed_electronic_refund_outcome_schemas import (
    MixedElectronicRefundConfirmation,
    ReconciledMixedElectronicRefund,
)
from app.domains.pos.mixed_electronic_refund_outcome_service import (
    MixedElectronicRefundOutcomeService,
)
from tests.domains.pos.test_mixed_electronic_refund_intent_service import Review


class Outcomes:
    def __init__(self, review: Review, saved: SavedMixedElectronicRefundActivation) -> None:
        self.review, self.saved = review, saved
        self.rows: tuple[MixedElectronicRefundOutcome, ...] = ()
        self.service = MixedElectronicRefundOutcomeService(
            MixedElectronicRefundOutcomeRepository(cast(AsyncSession, review.session))
        )
        self.service.activations.read = AsyncMock(return_value=saved)
        self.service.bindings.lock_operation = AsyncMock()
        parent = AsyncMock()
        parent.tenant_id, parent.id = review.tenant, review.parent
        parent.branch_id, parent.register_id = review.branch, saved.register_id
        self.service.authority.lock_sale = AsyncMock(return_value=parent)
        self.service.repository.list = AsyncMock(side_effect=self.list)
        self.service.repository.insert = AsyncMock(side_effect=self.insert)
        self.confirmation = MixedElectronicRefundConfirmation(
            original_payment_id=review.preview.payments[0].original_payment_id,
            terminal_id="TEST",
            document_number="DOC",
        )

    async def list(self, *args: object) -> tuple[MixedElectronicRefundOutcome, ...]:
        return self.rows

    async def insert(self, row: MixedElectronicRefundOutcome) -> None:
        row.created_at = datetime(2026, 1, 4, tzinfo=UTC)
        self.rows = (*self.rows, row)

    async def act(self, *, read: bool = False) -> ReconciledMixedElectronicRefund:
        args = {
            "tenant_id": self.review.tenant,
            "actor_id": self.review.actor,
            "parent_sale_id": self.review.parent,
            "operation_id": self.review.request.operation_id,
        }
        if read:
            return await self.service.read(**args, allowed_branch_ids={self.review.branch})
        return await self.service.record(
            **args, allowed_branch_ids={self.review.branch}, confirmation=self.confirmation
        )


@pytest.fixture
async def outcomes(monkeypatch: pytest.MonkeyPatch) -> Outcomes:
    review = Review(monkeypatch)
    saved = await review.save()
    activation = SavedMixedElectronicRefundActivation(
        operation_id=saved.operation_id,
        parent_sale_id=review.parent,
        actor_id=review.actor,
        branch_id=review.branch,
        register_id=uuid4(),
        shift_id=uuid4(),
        activated_at=datetime(2026, 1, 3, tzinfo=UTC),
        review=saved,
    )
    return Outcomes(review, activation)


async def test_record_replay_and_recover_no_second_insert(outcomes: Outcomes) -> None:
    before = await outcomes.act(read=True)
    assert before.state == "requires_reconciliation"
    saved = await outcomes.act()
    assert saved.state == "confirmed_evidence_only"
    assert saved.confirmations[0].amount == Decimal("0.99")
    assert saved.missing_payment_ids == ()
    assert await outcomes.act() == saved
    assert await outcomes.act(read=True) == saved
    outcomes.service.repository.insert.assert_awaited_once()


async def test_changed_document_conflict_and_unknown_payment(outcomes: Outcomes) -> None:
    await outcomes.act()
    outcomes.confirmation = outcomes.confirmation.model_copy(update={"document_number": "OTHER"})
    with pytest.raises(ConflictError):
        await outcomes.act()
    outcomes.confirmation = outcomes.confirmation.model_copy(
        update={"original_payment_id": uuid4()}
    )
    with pytest.raises(BusinessRuleError):
        await outcomes.act()


@pytest.mark.parametrize(
    "field",
    [
        "tenant_id",
        "operation_id",
        "actor_id",
        "branch_id",
        "parent_sale_id",
        "created_by",
        "original_payment_id",
    ],
)
async def test_recovery_rejects_corrupt_scope(outcomes: Outcomes, field: str) -> None:
    await outcomes.act()
    setattr(outcomes.rows[0], field, uuid4())
    with pytest.raises(AurumError, match="inconsistent"):
        await outcomes.act(read=True)


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": Decimal("0.98")},
        {"method": "cash"},
        {"currency": "USD"},
        {"terminal_id": " spaced "},
        {"created_at": datetime(2025, 1, 1, tzinfo=UTC)},
    ],
)
async def test_recovery_rejects_corrupt_evidence(
    outcomes: Outcomes, changes: dict[str, object]
) -> None:
    await outcomes.act()
    for name, value in changes.items():
        setattr(outcomes.rows[0], name, value)
    with pytest.raises(AurumError, match="inconsistent"):
        await outcomes.act(read=True)


async def test_duplicate_rows_rejected(outcomes: Outcomes) -> None:
    await outcomes.act()
    outcomes.rows = (*outcomes.rows, outcomes.rows[0])
    with pytest.raises(AurumError, match="inconsistent"):
        await outcomes.act(read=True)


@pytest.mark.parametrize("kind", ["new", "dirty", "deleted"])
async def test_record_rejects_unflushed_callers(outcomes: Outcomes, kind: str) -> None:
    getattr(outcomes.review.session, kind).add(object())
    with pytest.raises(ConflictError, match="Persist"):
        await outcomes.act()
    outcomes.service.repository.insert.assert_not_awaited()


@pytest.mark.parametrize("value", ["", " ", "x" * 65, "a\nb", "a\x7fb"])
def test_evidence_validation(value: str) -> None:
    with pytest.raises(ValidationError):
        MixedElectronicRefundConfirmation(
            original_payment_id=uuid4(), terminal_id=value, document_number="DOC"
        )


def test_evidence_normalization() -> None:
    confirmation = MixedElectronicRefundConfirmation(
        original_payment_id=uuid4(), terminal_id=" TEST ", document_number=" DOC "
    )
    assert confirmation.terminal_id == "TEST" and confirmation.document_number == "DOC"
