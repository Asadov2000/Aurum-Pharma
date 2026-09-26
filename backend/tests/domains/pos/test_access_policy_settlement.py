"""Administrative restrictions cannot interrupt external terminal settlement."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.tenant_access.repository import TenantAccessRepository
from app.domains.tenant_access.schemas import AccessPolicyUpdate
from app.domains.tenant_access.service import TenantAccessService
from tests.auth_helpers import create_support_access_token
from tests.domains.pos.test_payment_attempts import _draft_sale
from tests.domains.pos.test_refund import _open_shift_and_sell
from tests.platform_access_helpers import create_test_platform_user


@asynccontextmanager
async def _platform_policy(db: AsyncSession) -> AsyncIterator[TenantAccessService]:
    keys = (
        "app.user_id",
        "app.tenant_id",
        "app.auth_session_id",
        "app.mfa_verified_at",
        "app.support_session",
        "app.support_access_session_id",
    )
    previous = {
        key: str(await db.scalar(text("SELECT current_setting(:key,true)"), {"key": key}) or "")
        for key in keys
    }
    await db.execute(text("SELECT set_config('app.tenant_id','',true)"))
    await db.execute(text("SELECT set_config('app.support_access_session_id','',true)"))
    actor = await create_test_platform_user(db, access_kind="developer")
    claims = decode_access_token(await create_support_access_token(db, actor))
    context = {
        "app.user_id": str(actor.id),
        "app.tenant_id": "",
        "app.auth_session_id": str(claims["sid"]),
        "app.mfa_verified_at": str(claims["mfa_at"]),
        "app.support_session": "true",
        "app.support_access_session_id": "",
    }
    for key, value in context.items():
        await db.execute(text("SELECT set_config(:key,:value,true)"), {"key": key, "value": value})
    try:
        yield TenantAccessService(TenantAccessRepository(db))
    finally:
        for key, value in previous.items():
            await db.execute(
                text("SELECT set_config(:key,:value,true)"), {"key": key, "value": value}
            )


async def _assert_cannot_restrict(db: AsyncSession, tenant_id: UUID) -> None:
    async with _platform_policy(db) as policy:
        for restriction in (
            {"mode": "readonly"},
            {"blocked_modules": ["pos"]},
            {"blocked_modules": ["inventory"]},
        ):
            with pytest.raises(DBAPIError, match="tenant_access_pending_settlement"):
                async with db.begin_nested():
                    await policy.update(
                        tenant_id,
                        AccessPolicyUpdate.model_validate(
                            {
                                "expected_version": 1,
                                "reason": "Administrative restriction",
                                **restriction,
                            }
                        ),
                    )
        with pytest.raises(DBAPIError, match="tenant_access_pending_settlement"):
            async with db.begin_nested():
                await db.execute(
                    text("UPDATE tenant SET status='archived' WHERE id=:id"), {"id": tenant_id}
                )


async def test_external_payment_finishes_before_restriction_can_be_applied(
    db_session: AsyncSession, pos_scaffold
) -> None:
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    sale = await _draft_sale(service, scaffold)
    tenant_id, actor_id, sale_id = scaffold["tenant"].id, scaffold["cashier"].id, sale.id
    attempt = await service.create_payment_attempt(
        tenant_id=tenant_id,
        sale_id=sale_id,
        actor_id=actor_id,
        operation_id=uuid4(),
        payment_method="card",
        amount=Decimal("10"),
        currency="TJS",
    )
    attempt_id = attempt.id
    await _assert_cannot_restrict(db_session, tenant_id)
    await service.begin_payment_attempt_reconciliation(
        tenant_id=tenant_id, attempt_id=attempt_id, actor_id=actor_id
    )
    # This is the durable boundary immediately before the external terminal is called.
    await _assert_cannot_restrict(db_session, tenant_id)
    for _ in range(2):
        confirmed = await service.confirm_payment_attempt(
            tenant_id=tenant_id,
            attempt_id=attempt_id,
            actor_id=actor_id,
            terminal_id="ACCESS-TERMINAL",
            external_reference="ACCESS-PAYMENT",
        )
        assert confirmed.status == "confirmed"
    await _assert_cannot_restrict(db_session, tenant_id)
    operation_id = uuid4()
    checkout_args = {
        "tenant_id": tenant_id,
        "register_id": scaffold["register"].id,
        "cashier_user_id": actor_id,
        "operation_id": operation_id,
        "draft_sale_id": sale_id,
        "items": [(scaffold["item"].id, Decimal("1"))],
        "payments": [("card", Decimal("10"), None, attempt_id)],
    }
    first = await service.checkout(**checkout_args)
    second = await service.checkout(**checkout_args)
    assert first.sale_id == second.sale_id
    next_sale = await service.create_sale(
        tenant_id=tenant_id, register_id=scaffold["register"].id, cashier_user_id=actor_id
    )
    next_sale_id = next_sale.id
    await service.add_item(
        sale_id=next_sale_id, catalog_id=scaffold["item"].id, qty=Decimal("1"), actor_id=actor_id
    )
    async with _platform_policy(db_session) as policy:
        result = await policy.update(
            tenant_id,
            AccessPolicyUpdate(expected_version=1, reason="Safe after settlement", mode="readonly"),
        )
        assert result.mode == "readonly"
    with pytest.raises(DBAPIError, match="tenant_access_restricted"):
        async with db_session.begin_nested():
            await service.create_payment_attempt(
                tenant_id=tenant_id,
                sale_id=next_sale_id,
                actor_id=actor_id,
                operation_id=uuid4(),
                payment_method="card",
                amount=Decimal("10"),
                currency="TJS",
            )


async def test_external_refund_must_be_resolved_before_restriction(
    db_session: AsyncSession, pos_scaffold
) -> None:
    service, scaffold, parent, item = await _open_shift_and_sell(
        db_session, pos_scaffold, qty=1, payments=[("card", Decimal("10"))]
    )
    tenant_id, actor_id = scaffold["tenant"].id, scaffold["cashier"].id
    attempt = await service.create_refund_attempt(
        tenant_id=tenant_id,
        parent_sale_id=parent.id,
        items=[(item.id, Decimal("1"))],
        actor_id=actor_id,
        operation_id=uuid4(),
        reason="other",
    )
    attempt_id = attempt.id
    await _assert_cannot_restrict(db_session, tenant_id)
    await service.begin_refund_attempt_reconciliation(
        tenant_id=tenant_id, attempt_id=attempt_id, actor_id=actor_id, allowed_branch_ids=None
    )
    await _assert_cannot_restrict(db_session, tenant_id)
    for _ in range(2):
        confirmed = await service.confirm_refund_attempt(
            tenant_id=tenant_id,
            attempt_id=attempt_id,
            actor_id=actor_id,
            confirmations=[("card", "ACCESS-TERMINAL", "ACCESS-REFUND")],
            allowed_branch_ids=None,
        )
        assert confirmed.status == "confirmed"
    await _assert_cannot_restrict(db_session, tenant_id)
    parent_id, item_id = parent.id, item.id
    operation_id = uuid4()
    first = await service.refund(
        parent_sale_id=parent_id,
        items=[(item_id, Decimal("1"))],
        reason="other",
        comment=None,
        cashier_user_id=actor_id,
        operation_id=operation_id,
        refund_attempt_id=attempt_id,
    )
    second = await service.refund(
        parent_sale_id=parent_id,
        items=[(item_id, Decimal("1"))],
        reason="other",
        comment=None,
        cashier_user_id=actor_id,
        operation_id=operation_id,
        refund_attempt_id=attempt_id,
    )
    assert first.id == second.id
    async with _platform_policy(db_session) as policy:
        result = await policy.update(
            tenant_id,
            AccessPolicyUpdate(expected_version=1, reason="Safe after refund", mode="readonly"),
        )
        assert result.mode == "readonly"
