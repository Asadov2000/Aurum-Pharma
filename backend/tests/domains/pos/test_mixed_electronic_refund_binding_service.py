"""Binding retries recover state; cancellation is final without issuing payments."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Literal, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.domains.pos.mixed_electronic_refund_binding_models import (
    MixedElectronicRefundBinding,
    MixedElectronicRefundBindingCancel,
)
from app.domains.pos.mixed_electronic_refund_binding_repository import (
    MixedElectronicRefundBindingRepository,
)
from app.domains.pos.mixed_electronic_refund_binding_schemas import (
    MixedElectronicRefundBindingRead,
)
from app.domains.pos.mixed_electronic_refund_binding_service import (
    MixedElectronicRefundBindingService,
)
from app.domains.pos.mixed_electronic_refund_revalidation import (
    MixedElectronicRefundRevalidationService,
)
from app.domains.pos.mixed_return_journal_repository import MixedReturnJournalRepository
from tests.domains.pos.test_mixed_electronic_refund_intent_service import Review, Session
from tests.domains.pos.test_mixed_electronic_refund_revalidation_service import source


class NestedSession(Session):
    @property
    @contextmanager
    def no_autoflush(self) -> Iterator[None]:
        # SQLAlchemy restores the previous setting when nested contexts exit.
        previous = self.autoflush_disabled
        self.autoflush_disabled = True
        try:
            yield
        finally:
            self.autoflush_disabled = previous


class Binding:
    def __init__(self, review: Review, monkeypatch: pytest.MonkeyPatch) -> None:
        self.review = review
        self.service = MixedElectronicRefundBindingService(
            MixedElectronicRefundBindingRepository(cast(AsyncSession, review.session))
        )
        self.get = AsyncMock(return_value=(None, None))
        self.insert_binding = AsyncMock(side_effect=self.store_binding)
        self.insert_cancel = AsyncMock(side_effect=self.store_cancel)
        self.lock_operation = AsyncMock()
        self.lock_sale = AsyncMock(return_value=source(review))
        self.revalidate = AsyncMock(return_value=review.service._result(review.get.return_value))
        self.is_read_committed = AsyncMock(return_value=True)
        monkeypatch.setattr(MixedElectronicRefundBindingRepository, "get_state", self.get)
        monkeypatch.setattr(
            MixedElectronicRefundBindingRepository, "claim_binding", self.insert_binding
        )
        monkeypatch.setattr(
            MixedElectronicRefundBindingRepository, "claim_cancel", self.insert_cancel
        )
        monkeypatch.setattr(
            MixedElectronicRefundBindingRepository, "lock_operation", self.lock_operation
        )
        monkeypatch.setattr(
            MixedElectronicRefundBindingRepository, "is_read_committed", self.is_read_committed
        )
        monkeypatch.setattr(MixedReturnJournalRepository, "lock_sale", self.lock_sale)
        monkeypatch.setattr(
            MixedElectronicRefundRevalidationService, "revalidate_locked_review", self.revalidate
        )

    async def store_binding(
        self, value: MixedElectronicRefundBinding
    ) -> MixedElectronicRefundBinding:
        value.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        self.get.return_value = (value, self.get.return_value[1])
        return value

    async def store_cancel(
        self, value: MixedElectronicRefundBindingCancel
    ) -> MixedElectronicRefundBindingCancel:
        value.created_at = datetime(2026, 1, 2, tzinfo=UTC)
        self.get.return_value = (self.get.return_value[0], value)
        return value

    async def act(
        self, action: Literal["bind", "cancel", "read"]
    ) -> MixedElectronicRefundBindingRead:
        review = self.review
        return await {
            "bind": self.service.bind,
            "cancel": self.service.cancel,
            "read": self.service.read,
        }[action](
            tenant_id=review.tenant,
            actor_id=review.actor,
            allowed_branch_ids={review.branch},
            parent_sale_id=review.parent,
            operation_id=review.request.operation_id,
        )


@pytest.fixture
async def binding(monkeypatch: pytest.MonkeyPatch) -> Binding:
    review = Review(monkeypatch)
    review.session = NestedSession()
    review.service.repository.session = cast(AsyncSession, review.session)
    await review.save()
    review.quote.reset_mock()
    return Binding(review, monkeypatch)


async def test_bind_retry_and_read_restore_original_without_revalidation(binding: Binding) -> None:
    bound = await binding.act("bind")
    assert bound.state == "bound"
    assert bound.bound_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert bound.review_revision_hash == binding.review.preview.revision_hash
    assert await binding.act("bind") == bound
    assert await binding.act("read") == bound
    binding.insert_binding.assert_awaited_once()
    binding.insert_cancel.assert_not_awaited()
    binding.revalidate.assert_awaited_once()
    binding.review.quote.assert_not_awaited()


@pytest.mark.parametrize("bind_first", [False, True])
async def test_cancellation_is_final_and_recoverable(binding: Binding, bind_first: bool) -> None:
    if bind_first:
        await binding.act("bind")
    cancelled = await binding.act("cancel")
    assert cancelled.state == "cancelled"
    assert cancelled.cancelled_at == datetime(2026, 1, 2, tzinfo=UTC)
    assert (cancelled.bound_at is not None) == bind_first
    assert await binding.act("cancel") == cancelled
    assert await binding.act("read") == cancelled
    with pytest.raises(ConflictError):
        await binding.act("bind")
    binding.insert_cancel.assert_awaited_once()
    assert binding.insert_binding.await_count == binding.revalidate.await_count == int(bind_first)
    binding.review.quote.assert_not_awaited()


async def test_stale_review_is_not_bound_but_may_be_cancelled(binding: Binding) -> None:
    binding.revalidate.side_effect = ConflictError("history changed")
    with pytest.raises(ConflictError):
        await binding.act("bind")
    binding.insert_binding.assert_not_awaited()
    assert (await binding.act("cancel")).state == "cancelled"


@pytest.mark.parametrize("action", ["bind", "cancel"])
@pytest.mark.parametrize("pending", ["new", "dirty", "deleted"])
async def test_mutation_rejects_unrelated_pending_orm_changes(
    binding: Binding, action: Literal["bind", "cancel"], pending: str
) -> None:
    getattr(binding.review.session, pending).add(object())
    with pytest.raises(ConflictError):
        await binding.act(action)
    binding.lock_operation.assert_not_awaited()
    binding.revalidate.assert_not_awaited()
    binding.insert_binding.assert_not_awaited()
    binding.insert_cancel.assert_not_awaited()


async def test_read_does_not_flush_dirty_session(binding: Binding) -> None:
    saved = await binding.act("bind")
    binding.review.session.dirty.add(object())
    state = binding.get.return_value

    async def guarded(*args: object) -> object:
        assert binding.review.session.autoflush_disabled
        return state

    binding.get.side_effect = guarded
    assert await binding.act("read") == saved
    assert not binding.review.session.autoflush_disabled
    binding.insert_binding.assert_awaited_once()


@pytest.mark.parametrize("action", ["bind", "cancel", "read"])
async def test_every_replay_rechecks_saved_review_authority(
    binding: Binding, action: Literal["bind", "cancel", "read"]
) -> None:
    await binding.act("bind")
    binding.review.authority.side_effect = PermissionDeniedError("revoked")
    with pytest.raises(PermissionDeniedError):
        await binding.act(action)
    binding.insert_binding.assert_awaited_once()
    binding.insert_cancel.assert_not_awaited()


@pytest.mark.parametrize("scope", ["actor", "branch", "parent", "tenant"])
async def test_recovery_cannot_escape_saved_review_scope(binding: Binding, scope: str) -> None:
    await binding.act("bind")
    if scope == "tenant":
        binding.review.tenant = uuid4()
        binding.review.get.return_value = None
    else:
        setattr(binding.review, scope, uuid4())
    expected = NotFoundError if scope in {"parent", "tenant"} else PermissionDeniedError
    with pytest.raises(expected):
        await binding.act("read")


async def test_cancel_requires_read_committed_before_writing(binding: Binding) -> None:
    binding.is_read_committed.return_value = False
    with pytest.raises(BusinessRuleError):
        await binding.act("cancel")
    binding.insert_cancel.assert_not_awaited()


async def test_read_of_review_without_binding_is_not_found(binding: Binding) -> None:
    with pytest.raises(NotFoundError):
        await binding.act("read")
    binding.revalidate.assert_not_awaited()
