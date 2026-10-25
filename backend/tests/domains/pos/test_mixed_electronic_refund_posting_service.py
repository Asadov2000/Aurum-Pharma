"""Pure posting boundaries reject unsafe session state and foreign shift scope."""

from typing import NoReturn, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.domains.pos.mixed_electronic_refund_posting_repository import (
    MixedElectronicRefundPostingRepository,
)
from app.domains.pos.mixed_electronic_refund_posting_service import (
    MixedElectronicRefundPostingService,
)
from app.domains.pos.models import Sale, Shift


@pytest.mark.parametrize("pending", ["new", "dirty", "deleted"])
async def test_pending_changes_are_rejected_before_transaction_or_flush(pending: str) -> None:
    session = MagicMock(spec=AsyncSession, new=set(), dirty=set(), deleted=set())
    setattr(session, pending, {object()})
    session.begin_nested.side_effect = AssertionError("Pending changes must not reach a savepoint")
    service = MixedElectronicRefundPostingService(
        MixedElectronicRefundPostingRepository(cast(AsyncSession, session))
    )
    with pytest.raises(ConflictError, match="Persist pending changes"):
        await service.post(
            tenant_id=uuid4(),
            actor_id=uuid4(),
            allowed_branch_ids={uuid4()},
            parent_sale_id=uuid4(),
            operation_id=uuid4(),
            shift_id=uuid4(),
        )
    session.begin_nested.assert_not_called()
    session.flush.assert_not_called()


@pytest.mark.parametrize("field", ["opened_by_user_id", "register_id", "currency"])
async def test_new_posting_rejects_wrong_shift_owner_register_or_currency(field: str) -> None:
    tenant, actor, branch, register = uuid4(), uuid4(), uuid4(), uuid4()
    parent = Sale(id=uuid4(), tenant_id=tenant, branch_id=branch, register_id=register)
    shift = Shift(
        id=uuid4(),
        tenant_id=tenant,
        branch_id=branch,
        register_id=register,
        opened_by_user_id=actor,
        status="open",
        closed_at=None,
        currency="TJS",
    )
    setattr(shift, field, "USD" if field == "currency" else uuid4())
    async with AsyncSession() as session:
        service = MixedElectronicRefundPostingService(
            MixedElectronicRefundPostingRepository(session)
        )
        lock_shift = AsyncMock(return_value=shift)
        register_check = AsyncMock(side_effect=AssertionError("Invalid shift passed its guard"))
        service.financial.lock_shift = lock_shift
        service.financial.lock_active_register = register_check
        with pytest.raises(BusinessRuleError, match="your open original-register shift"):
            await service._assert_new_post(tenant, actor, parent, shift.id, None, None)
        lock_shift.assert_awaited_once_with(tenant, shift.id)
        register_check.assert_not_awaited()


async def test_read_suppresses_autoflush_and_restores_setting_after_lookup_failure() -> None:
    async with AsyncSession() as session:
        pending = Sale(id=uuid4())
        session.add(pending)
        service = MixedElectronicRefundPostingService(
            MixedElectronicRefundPostingRepository(session)
        )

        async def unavailable(**_scope: object) -> NoReturn:
            assert session.autoflush is False
            raise NotFoundError("Synthetic missing activation")

        service.outcomes.read = AsyncMock(side_effect=unavailable)
        with pytest.raises(NotFoundError, match="Synthetic missing activation"):
            await service.read(
                tenant_id=uuid4(),
                actor_id=uuid4(),
                allowed_branch_ids={uuid4()},
                parent_sale_id=uuid4(),
                operation_id=uuid4(),
            )
        assert session.autoflush is True
        assert pending in session.new
