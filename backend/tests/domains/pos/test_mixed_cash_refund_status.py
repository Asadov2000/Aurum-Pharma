"""Recovery verifies immutable bindings and current rights without more effects."""

from copy import deepcopy
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AurumError, BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.pos.mixed_cash_refund_status_repository import (
    MixedCashRefundStatusRepository,
    MixedCashRefundStatusSnapshot,
)
from app.domains.pos.mixed_cash_refund_status_schemas import (
    MixedRefundOperationCompleted,
    MixedRefundOperationNotFound,
)
from app.domains.pos.mixed_cash_refund_status_service import MixedCashRefundStatusService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos.test_mixed_cash_refund import facts, refund
from tests.domains.pos.test_mixed_return_journal import mixed_source
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def read(
    session: AsyncSession,
    parent_sale_id: UUID,
    operation_id: UUID,
    *,
    actor_id: UUID | None = None,
    tenant_id: UUID | None = None,
    branches: set[UUID] | None = None,
    repository: MixedCashRefundStatusRepository | None = None,
) -> MixedRefundOperationCompleted | MixedRefundOperationNotFound:
    context = runtime(session)
    return await MixedCashRefundStatusService(
        repository or MixedCashRefundStatusRepository(session)
    ).read(
        tenant_id=tenant_id or context.stock.tenant_id,
        parent_sale_id=parent_sale_id,
        operation_id=operation_id,
        actor_id=actor_id or context.actor_id,
        allowed_branch_ids=branches,
    )


def assert_no_private_fields(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            assert "cost" not in str(key).lower()
            assert key not in {"result_payload", "receipt_payload", "request_hash"}
            assert_no_private_fields(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_private_fields(child)


@pytest.mark.parametrize("indices", [(0,), (1,), (0, 1)])
async def test_repeated_recovery_returns_only_immutable_retail_facts(
    db_session: AsyncSession, indices: tuple[int, ...]
) -> None:
    source = await mixed_source(db_session)
    selections = source.selections()
    result = await refund(db_session, source, tuple(selections[index] for index in indices))
    before = await facts(db_session)
    recovered = await read(db_session, result.parent_sale_id, result.operation_id)
    assert isinstance(recovered, MixedRefundOperationCompleted)
    assert recovered.model_dump(mode="json") == {
        "state": "completed",
        "operation_id": str(result.operation_id),
        "parent_sale_id": str(result.parent_sale_id),
        "sale_id": str(result.sale_id),
        "total_amount": str(result.total_amount),
        "receipt": result.receipt.model_dump(mode="json"),
    }
    assert_no_private_fields(recovered.model_dump(mode="json"))
    assert await read(db_session, result.parent_sale_id, result.operation_id) == recovered
    assert await facts(db_session) == before


async def test_closed_shift_does_not_require_new_shift_or_payout(db_session: AsyncSession) -> None:
    result = await refund(db_session, await mixed_source(db_session))
    await close_shift(db_session, result.shift_id)
    before = await facts(db_session)
    recovered = await read(db_session, result.parent_sale_id, result.operation_id)
    assert isinstance(recovered, MixedRefundOperationCompleted)
    assert recovered.receipt == result.receipt
    assert await facts(db_session) == before


async def test_readonly_tenant_can_recover_without_writes(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    result = await refund(db_session, await mixed_source(db_session))
    context = runtime(db_session)
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"),
            {"id": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await facts(db_session)
    recovered = await read(db_session, result.parent_sale_id, result.operation_id)
    assert isinstance(recovered, MixedRefundOperationCompleted)
    assert recovered.receipt == result.receipt
    assert await facts(db_session) == before


async def test_unknown_operation_and_other_mixed_parent_are_hidden(
    db_session: AsyncSession,
) -> None:
    result = await refund(db_session, await mixed_source(db_session))
    other = await mixed_source(db_session)
    before = await facts(db_session)
    for parent_id, operation_id in (
        (result.parent_sale_id, uuid4()),
        (other.original.sale_id, result.operation_id),
    ):
        assert await read(db_session, parent_id, operation_id) == MixedRefundOperationNotFound(
            operation_id=operation_id, parent_sale_id=parent_id
        )
    with pytest.raises(NotFoundError):
        await read(db_session, uuid4(), result.operation_id)
    assert await facts(db_session) == before


async def test_non_mixed_parent_is_rejected_before_not_found(db_session: AsyncSession) -> None:
    source = await checkout_source(db_session)
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError, match="original mixed"):
        await read(db_session, source.sale.id, uuid4())
    assert await facts(db_session) == before


@pytest.mark.parametrize("operation_known", [False, True])
@pytest.mark.parametrize("failure", ["actor", "tenant", "branch", "empty_branch"])
async def test_current_identity_and_scope_are_required_before_known_or_unknown_results(
    db_session: AsyncSession, operation_known: bool, failure: str
) -> None:
    result = await refund(db_session, await mixed_source(db_session))
    before = await facts(db_session)
    with pytest.raises(PermissionDeniedError):
        await read(
            db_session,
            result.parent_sale_id,
            result.operation_id if operation_known else uuid4(),
            actor_id=uuid4() if failure == "actor" else None,
            tenant_id=uuid4() if failure == "tenant" else None,
            branches=(
                {uuid4()} if failure == "branch" else set() if failure == "empty_branch" else None
            ),
        )
    assert await facts(db_session) == before


@pytest.mark.parametrize("permission", ["pos.sell", "pos.refund"])
async def test_actual_permission_is_checked_and_other_cashier_cannot_read_saved_refund(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    permission: str,
) -> None:
    result = await refund(db_session, await mixed_source(db_session))
    context = runtime(db_session)
    before = await facts(db_session)
    await db_session.commit()
    actor_id = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=(permission,)
    )
    await set_receiving_context(db_session, context.stock, actor_id)
    if permission == "pos.sell":
        with pytest.raises(PermissionDeniedError):
            await read(db_session, result.parent_sale_id, result.operation_id, actor_id=actor_id)
        with pytest.raises(PermissionDeniedError):
            await read(db_session, result.parent_sale_id, uuid4(), actor_id=actor_id)
    else:
        assert await read(
            db_session, result.parent_sale_id, result.operation_id, actor_id=actor_id
        ) == MixedRefundOperationNotFound(
            operation_id=result.operation_id, parent_sale_id=result.parent_sale_id
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before


async def test_actual_other_tenant_cannot_recover_saved_refund(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    result = await refund(db_session, await mixed_source(db_session))
    context = runtime(db_session)
    before = await facts(db_session)
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        foreign = await seed_ledger_context(support)
    actor_id = await seed_receiving_actor(
        db_engine, maintenance_engine, foreign, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, foreign, actor_id)
    with pytest.raises(NotFoundError):
        await read(
            db_session,
            result.parent_sale_id,
            result.operation_id,
            actor_id=actor_id,
            tenant_id=foreign.tenant_id,
            branches={foreign.branch_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before


async def test_own_recovery_needs_refund_permission_without_sell_permission(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    result = await refund(db_session, await mixed_source(db_session))
    context = runtime(db_session)
    await db_session.commit()
    refund_only = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text(
                "UPDATE user_assignment SET role_id=(SELECT role_id FROM user_assignment "
                "WHERE user_id=:other AND tenant_id=:tenant AND branch_id=:branch) "
                "WHERE user_id=:actor AND tenant_id=:tenant AND branch_id=:branch"
            ),
            {
                "other": refund_only,
                "actor": context.actor_id,
                "tenant": context.stock.tenant_id,
                "branch": context.stock.branch_id,
            },
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert (
        await db_session.scalar(
            text("SELECT tenant_actor_has_scoped_permission(:tenant,'pos.sell',:branch)"),
            {"tenant": context.stock.tenant_id, "branch": context.stock.branch_id},
        )
        is False
    )
    before = await facts(db_session)
    recovered = await read(
        db_session, result.parent_sale_id, result.operation_id, branches={result.branch_id}
    )
    assert isinstance(recovered, MixedRefundOperationCompleted)
    assert recovered.receipt == result.receipt
    assert await facts(db_session) == before


class SnapshotRepository(MixedCashRefundStatusRepository):
    def __init__(self, session: AsyncSession, snapshot: MixedCashRefundStatusSnapshot) -> None:
        super().__init__(session)
        self.snapshot = snapshot

    async def read(
        self, tenant_id: UUID, parent_sale_id: UUID, operation_id: UUID
    ) -> MixedCashRefundStatusSnapshot | None:
        return self.snapshot


@pytest.mark.parametrize(
    "component,field,value",
    [
        ("parent", "is_test", True),
        ("checkout", "receipt_payload", {}),
        ("checkout", "cash_intent_id", UUID(int=1)),
        ("marker", "result_payload", {}),
        ("marker", "receipt_payload", {}),
        ("marker", "request_hash", "0" * 64),
        ("returned_sale", "status", "draft"),
        ("returned_sale", "receipt_snapshot", {}),
        ("returned_sale", "parent_sale_id", UUID(int=1)),
        ("payment", "payment_method", "card"),
        ("payment", "operation_id", UUID(int=1)),
        ("payment", "metadata_json", {}),
        ("journal", "result_payload", {}),
        ("journal", "parent_checkout_id", UUID(int=1)),
        ("journal", "state", "draft"),
        ("journal", "request_hash", "0" * 64),
    ],
)
async def test_inconsistent_stored_bindings_fail_closed_without_exposing_payload(
    db_session: AsyncSession, component: str, field: str, value: object
) -> None:
    result = await refund(db_session, await mixed_source(db_session))
    snapshot = await MixedCashRefundStatusRepository(db_session).read(
        result.tenant_id, result.parent_sale_id, result.operation_id
    )
    assert snapshot is not None
    model = getattr(snapshot, component)
    assert model is not None
    # A detached, independent object represents corrupt persisted data. The real
    # immutable rows are never modified and no corrupted object enters the session.
    changed = type(model)(
        **{
            column.key: deepcopy(getattr(model, column.key))
            for column in inspect(model).mapper.attrs
        }
    )
    setattr(changed, field, value)
    altered = replace(snapshot, **{component: changed})
    before = await facts(db_session)
    with pytest.raises(AurumError, match="inconsistent"):
        await read(
            db_session,
            result.parent_sale_id,
            result.operation_id,
            repository=SnapshotRepository(db_session, altered),
        )
    assert await facts(db_session) == before


@pytest.mark.parametrize("component", ["returned_sale", "payment", "journal"])
async def test_missing_immutable_binding_does_not_become_success(
    db_session: AsyncSession, component: str
) -> None:
    result = await refund(db_session, await mixed_source(db_session))
    snapshot = await MixedCashRefundStatusRepository(db_session).read(
        result.tenant_id, result.parent_sale_id, result.operation_id
    )
    assert snapshot is not None
    before = await facts(db_session)
    with pytest.raises(AurumError, match="inconsistent"):
        await read(
            db_session,
            result.parent_sale_id,
            result.operation_id,
            repository=SnapshotRepository(db_session, replace(snapshot, **{component: None})),
        )
    assert await facts(db_session) == before
