"""Activation retries recover a marker; they never repeat authority-to-pay checks."""

from datetime import UTC, datetime
from typing import Literal, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AurumError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.pos.mixed_electronic_refund_activation_models import (
    MixedElectronicRefundActivation,
)
from app.domains.pos.mixed_electronic_refund_activation_repository import (
    MixedElectronicRefundActivationRepository,
)
from app.domains.pos.mixed_electronic_refund_activation_schemas import (
    SavedMixedElectronicRefundActivation,
)
from app.domains.pos.mixed_electronic_refund_activation_service import (
    MixedElectronicRefundActivationService,
)
from app.domains.pos.models import Shift
from app.domains.pos.physical_cash_refund_repository import PhysicalCashRefundRepository
from tests.domains.pos.test_mixed_electronic_refund_binding_service import (
    Binding,
    NestedSession,
)
from tests.domains.pos.test_mixed_electronic_refund_intent_service import Review


class Activation:
    def __init__(self, binding: Binding, monkeypatch: pytest.MonkeyPatch) -> None:
        self.binding = binding
        self.review = binding.review
        self.register_id, self.shift_id = uuid4(), uuid4()
        binding.lock_sale.return_value.register_id = self.register_id
        self.service = MixedElectronicRefundActivationService(
            MixedElectronicRefundActivationRepository(cast(AsyncSession, self.review.session))
        )
        self.get = AsyncMock(return_value=None)
        self.insert = AsyncMock(side_effect=self.store)
        self.shift = AsyncMock(
            return_value=Shift(
                id=self.shift_id,
                tenant_id=self.review.tenant,
                branch_id=self.review.branch,
                register_id=self.register_id,
                opened_by_user_id=self.review.actor,
                status="open",
                currency="TJS",
            )
        )
        self.register = AsyncMock(return_value=True)
        monkeypatch.setattr(MixedElectronicRefundActivationRepository, "get", self.get)
        monkeypatch.setattr(MixedElectronicRefundActivationRepository, "insert", self.insert)
        monkeypatch.setattr(PhysicalCashRefundRepository, "lock_shift", self.shift)
        monkeypatch.setattr(PhysicalCashRefundRepository, "lock_active_register", self.register)

    async def store(self, marker: MixedElectronicRefundActivation) -> None:
        marker.created_at = datetime(2026, 1, 3, tzinfo=UTC)
        self.get.return_value = marker

    async def act(
        self, action: Literal["start", "read"] = "start"
    ) -> SavedMixedElectronicRefundActivation:
        review = self.review
        if action == "read":
            return await self.service.read(
                tenant_id=review.tenant,
                actor_id=review.actor,
                allowed_branch_ids={review.branch},
                parent_sale_id=review.parent,
                operation_id=review.request.operation_id,
            )
        return await self.service.start(
            tenant_id=review.tenant,
            actor_id=review.actor,
            allowed_branch_ids={review.branch},
            parent_sale_id=review.parent,
            operation_id=review.request.operation_id,
            shift_id=self.shift_id,
        )


@pytest.fixture
async def activation(monkeypatch: pytest.MonkeyPatch) -> Activation:
    review = Review(monkeypatch)
    review.session = NestedSession()
    review.service.repository.session = cast(AsyncSession, review.session)
    await review.save()
    binding = Binding(review, monkeypatch)
    await binding.act("bind")
    binding.revalidate.reset_mock()
    return Activation(binding, monkeypatch)


async def test_activation_retry_and_read_preserve_marker_without_revalidation(
    activation: Activation,
) -> None:
    saved = await activation.act()
    assert saved.state == "activation_saved"
    assert saved.operation_id == activation.review.request.operation_id
    assert saved.shift_id == activation.shift_id
    assert saved.register_id == activation.register_id
    assert saved.activated_at == datetime(2026, 1, 3, tzinfo=UTC)
    assert saved.review == await activation.review.read()
    assert await activation.act() == saved
    assert await activation.act("read") == saved
    activation.insert.assert_awaited_once()
    activation.binding.revalidate.assert_awaited_once()
    activation.shift.assert_awaited_once()
    activation.register.assert_awaited_once()


async def test_retry_does_not_need_open_shift_or_active_register(activation: Activation) -> None:
    saved = await activation.act()
    activation.shift.side_effect = AssertionError("Replay must not reauthorize the old shift")
    activation.register.side_effect = AssertionError("Replay must not reauthorize old register")
    activation.binding.revalidate.side_effect = ConflictError("New history must not rewrite marker")
    assert await activation.act() == saved
    assert await activation.act("read") == saved
    activation.insert.assert_awaited_once()


async def test_changed_shift_under_same_operation_conflicts(activation: Activation) -> None:
    saved = await activation.act()
    activation.shift_id = uuid4()
    with pytest.raises(ConflictError):
        await activation.act()
    assert await activation.act("read") == saved
    activation.insert.assert_awaited_once()


@pytest.mark.parametrize("state", ["missing", "cancelled"])
async def test_unbound_or_cancelled_review_cannot_activate(
    activation: Activation, state: str
) -> None:
    if state == "missing":
        activation.binding.get.return_value = (None, None)
    else:
        await activation.binding.act("cancel")
    with pytest.raises(ConflictError):
        await activation.act()
    activation.insert.assert_not_awaited()


async def test_stale_history_cannot_activate(activation: Activation) -> None:
    activation.binding.revalidate.side_effect = ConflictError("history changed")
    with pytest.raises(ConflictError):
        await activation.act()
    activation.insert.assert_not_awaited()


@pytest.mark.parametrize(
    "field",
    ["id", "tenant_id", "branch_id", "register_id", "opened_by_user_id", "status", "currency"],
)
async def test_shift_must_be_own_open_original_register_shift(
    activation: Activation, field: str
) -> None:
    value = "closed" if field == "status" else "USD" if field == "currency" else uuid4()
    setattr(activation.shift.return_value, field, value)
    with pytest.raises(AurumError):
        await activation.act()
    activation.insert.assert_not_awaited()


async def test_missing_shift_cannot_activate(activation: Activation) -> None:
    activation.shift.return_value = None
    with pytest.raises(AurumError):
        await activation.act()
    activation.insert.assert_not_awaited()


async def test_inactive_register_cannot_activate(activation: Activation) -> None:
    activation.register.return_value = False
    with pytest.raises(AurumError):
        await activation.act()
    activation.insert.assert_not_awaited()


@pytest.mark.parametrize("pending", ["new", "dirty", "deleted"])
async def test_start_rejects_pending_work_without_locks_or_flush(
    activation: Activation, pending: str
) -> None:
    activation.binding.lock_operation.reset_mock()
    getattr(activation.review.session, pending).add(object())
    with pytest.raises(ConflictError):
        await activation.act()
    activation.binding.lock_operation.assert_not_awaited()
    activation.insert.assert_not_awaited()
    activation.shift.assert_not_awaited()


async def test_read_disables_autoflush_for_dirty_session(activation: Activation) -> None:
    saved = await activation.act()
    marker = activation.get.return_value
    activation.review.session.dirty.add(object())

    async def guarded(*args: object) -> MixedElectronicRefundActivation:
        assert activation.review.session.autoflush_disabled
        return cast(MixedElectronicRefundActivation, marker)

    activation.get.side_effect = guarded
    assert await activation.act("read") == saved
    assert not activation.review.session.autoflush_disabled
    activation.insert.assert_awaited_once()


@pytest.mark.parametrize("action", ["start", "read"])
async def test_current_refund_permission_required_on_each_recovery(
    activation: Activation, action: Literal["start", "read"]
) -> None:
    await activation.act()
    activation.review.authority.side_effect = PermissionDeniedError("revoked")
    with pytest.raises(PermissionDeniedError):
        await activation.act(action)
    activation.insert.assert_awaited_once()


@pytest.mark.parametrize(
    "field", ["tenant_id", "id", "parent_sale_id", "branch_id", "actor_id", "created_by"]
)
async def test_corrupt_marker_scope_fails_closed(activation: Activation, field: str) -> None:
    await activation.act()
    setattr(activation.get.return_value, field, uuid4())
    with pytest.raises(AurumError):
        await activation.act("read")


async def test_review_without_activation_is_not_found(activation: Activation) -> None:
    with pytest.raises(NotFoundError):
        await activation.act("read")
    activation.insert.assert_not_awaited()
