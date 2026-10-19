"""A saved review is re-quoted after locking; it never becomes payout evidence."""

from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.pos.models import Sale
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository
from app.domains.pos.unit_electronic_refund_intent_repository import (
    UnitElectronicRefundIntentRepository,
)
from app.domains.pos.unit_electronic_refund_revalidation import (
    UnitElectronicRefundRevalidationService,
)
from tests.domains.pos.test_unit_electronic_refund_intent_service import Review


@pytest.fixture
def review(monkeypatch: pytest.MonkeyPatch) -> Review:
    return Review(monkeypatch)


def source(review: Review) -> Sale:
    return Sale(
        id=review.parent,
        tenant_id=review.tenant,
        branch_id=review.branch,
        status="completed",
        sale_type="sale",
        parent_sale_id=None,
        is_test=False,
        currency="TJS",
    )


async def revalidate(review: Review) -> object:
    return await UnitElectronicRefundRevalidationService(
        review.service.repository
    ).revalidate_locked_review(
        tenant_id=review.tenant,
        actor_id=review.actor,
        allowed_branch_ids={review.branch},
        parent_sale_id=review.parent,
        operation_id=review.request.operation_id,
    )


@pytest.fixture
def lock(monkeypatch: pytest.MonkeyPatch, review: Review) -> AsyncMock:
    locked = AsyncMock(return_value=source(review))
    monkeypatch.setattr(PhysicalReturnJournalRepository, "lock_sale", locked)
    monkeypatch.setattr(
        UnitElectronicRefundIntentRepository, "is_read_committed", AsyncMock(return_value=True)
    )
    return locked


async def test_fresh_quote_is_after_lock_and_every_repeat_rechecks_history(
    review: Review, lock: AsyncMock
) -> None:
    saved = await review.save()
    review.quote.reset_mock()

    async def quote(**kwargs: object) -> object:
        assert lock.await_count > review.quote.await_count - 1
        assert review.session.autoflush_disabled
        assert kwargs["request"] == review.request.request.selection
        return review.preview

    review.quote.side_effect = quote
    assert await revalidate(review) == saved
    assert await revalidate(review) == saved
    assert lock.await_count == review.quote.await_count == 2
    lock.assert_awaited_with(review.tenant, review.parent)
    review.insert.assert_awaited_once()  # Only the original save.


@pytest.mark.parametrize("difference", ["revision", "amount", "payment_id", "offset", "branch"])
async def test_changed_history_is_rejected_even_when_total_is_unchanged(
    review: Review, lock: AsyncMock, difference: str
) -> None:
    await review.save()
    updates: dict[str, object]
    if difference == "revision":
        updates = {"revision_hash": "b" * 64}
    elif difference == "amount":
        updates = {"total_amount": Decimal("1.00")}
    elif difference == "payment_id":
        updates = {
            "payments": (
                review.preview.payments[0].model_copy(update={"original_payment_id": uuid4()}),
            )
        }
    elif difference == "offset":
        updates = {"lines": (review.preview.lines[0].model_copy(update={"offset": 1}),)}
    else:
        updates = {"branch_id": uuid4()}
    review.quote.return_value = review.preview.model_copy(update=updates)
    with pytest.raises(ConflictError, match="history changed"):
        await revalidate(review)
    # Historical recovery deliberately still works; it is not a reservation.
    assert await review.read() is not None
    review.insert.assert_awaited_once()
    lock.assert_awaited_once()


@pytest.mark.parametrize("pending", ["new", "dirty", "deleted"])
async def test_unrelated_changes_are_not_flushed(
    review: Review, lock: AsyncMock, pending: str
) -> None:
    await review.save()
    getattr(review.session, pending).add(object())
    with pytest.raises(ConflictError, match="pending changes"):
        await revalidate(review)
    lock.assert_not_awaited()
    assert review.quote.await_count == 1


async def test_repeatable_snapshot_is_rejected_before_lock_or_quote(
    review: Review, lock: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    await review.save()
    monkeypatch.setattr(
        UnitElectronicRefundIntentRepository, "is_read_committed", AsyncMock(return_value=False)
    )
    with pytest.raises(BusinessRuleError, match="READ COMMITTED"):
        await revalidate(review)
    lock.assert_not_awaited()
    assert review.quote.await_count == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", uuid4()),
        ("id", uuid4()),
        ("branch_id", uuid4()),
        ("status", "draft"),
        ("sale_type", "return"),
        ("parent_sale_id", uuid4()),
        ("is_test", True),
        ("currency", "USD"),
    ],
)
async def test_locked_parent_must_match_the_saved_original(
    review: Review, lock: AsyncMock, field: str, value: object
) -> None:
    await review.save()
    setattr(lock.return_value, field, value)
    with pytest.raises(ConflictError, match="source differs"):
        await revalidate(review)
    assert review.quote.await_count == 1


async def test_missing_parent_fails_closed(review: Review, lock: AsyncMock) -> None:
    await review.save()
    lock.return_value = None
    with pytest.raises(NotFoundError):
        await revalidate(review)
    assert review.quote.await_count == 1


@pytest.mark.parametrize("check", [1, 2, 3])
async def test_current_permission_required_before_and_after_wait_and_quote(
    review: Review, lock: AsyncMock, check: int
) -> None:
    await review.save()
    review.authority.reset_mock()
    review.authority.side_effect = [None] * (check - 1) + [PermissionDeniedError("revoked")]
    with pytest.raises(PermissionDeniedError):
        await revalidate(review)
    assert lock.await_count == (0 if check == 1 else 1)
    assert review.quote.await_count == (2 if check == 3 else 1)


async def test_another_author_cannot_lock_a_saved_review(review: Review, lock: AsyncMock) -> None:
    await review.save()
    review.actor = uuid4()
    with pytest.raises(PermissionDeniedError):
        await revalidate(review)
    lock.assert_not_awaited()
