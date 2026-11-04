"""A cancelled cash UUID cannot become a later payment or stock posting."""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.deps import get_db
from app.domains.inventory.unit_dispensing_v2_retail import UnitPricedCartLineV2
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.pos.unit_cash_intent_service import cash_intent_request_hash
from app.main import app
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_cost_ledger_v2 import priced_source
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.inventory.test_unit_ledger_v2_dispensing import remaining
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_physical_cash_checkout import facts
from tests.domains.pos.test_physical_cash_checkout_api import cash_path, quoted_request
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import (
    PhysicalCheckoutSource,
    checkout_source,
)
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


@dataclass(frozen=True)
class Attempt:
    source: PhysicalCheckoutSource
    request: CashConfirmationRequestV2
    item_id: UUID

    @property
    def path(self) -> str:
        return cash_path(self.source.sale.id)

    @property
    def status_path(self) -> str:
        return f"{self.path}/operations/{self.request.operation_id}"


async def saved_attempt(client: AsyncClient, session: AsyncSession, *, units: int = 3) -> Attempt:
    source = await checkout_source(session, quantity=2)
    item = await put_physical(session, source.sale.id, quantity=units)
    async with cart_actor(session):
        request = await quoted_request(client, source.sale.id)
        response = await client.post(
            f"{cash_path(source.sale.id)}/intent", json=request.model_dump(mode="json")
        )
        assert response.status_code == 200
    return Attempt(source, request, item.id)


async def financial_digest(session: AsyncSession) -> str:
    snapshot = await facts(session)
    # Cancellation adds its own immutable audit row, never a financial mutation.
    snapshot.pop("audit_log")
    payload = json.dumps(snapshot, default=str, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


async def cancellation_count(session: AsyncSession, attempt: Attempt) -> int:
    return int(
        await session.scalar(
            text("SELECT count(*) FROM pos_physical_cash_cancellation WHERE id=:id"),
            {"id": attempt.request.operation_id},
        )
        or 0
    )


async def insert_cancellation(session: AsyncSession, attempt: Attempt, **overrides: object) -> None:
    sale = attempt.source.sale
    values: dict[str, object] = {
        "tenant": sale.tenant_id,
        "id": attempt.request.operation_id,
        "sale": sale.id,
        "branch": sale.branch_id,
        "register": sale.register_id,
        "shift": sale.shift_id,
        "cashier": sale.cashier_user_id,
        "hash": cash_intent_request_hash(attempt.request),
        "author": sale.cashier_user_id,
    }
    values.update(overrides)
    await session.execute(
        text(
            "INSERT INTO pos_physical_cash_cancellation "
            "(tenant_id,id,sale_id,branch_id,register_id,shift_id,"
            "cashier_user_id,request_hash,created_by) "
            "VALUES(:tenant,:id,:sale,:branch,:register,:shift,:cashier,:hash,:author)"
        ),
        values,
    )


async def test_insufficient_cash_can_be_cancelled_and_recovered_without_financial_effects(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    attempt = await saved_attempt(client, db_session, units=61)
    body = attempt.request.model_dump(mode="json")
    before = await financial_digest(db_session)
    async with cart_actor(db_session):
        refused = await client.post(f"{attempt.path}/complete", json=body)
        assert refused.status_code == 422
        assert (
            refused.json()["error"]["message"] == "Insufficient compatible physical stock for cart"
        )
        assert await financial_digest(db_session) == before
        cancelled = await client.post(f"{attempt.path}/cancel", json=body)
        assert cancelled.status_code == 200
        assert cancelled.headers["cache-control"] == "private, no-store"
        result = cancelled.json()
        assert result["state"] == "cancelled"
        assert result["operation_id"] == str(attempt.request.operation_id)
        assert result["sale_id"] == str(attempt.source.sale.id)
        assert "result" not in result
        # A lost POST response is reconciled by this read, not another payment.
        recovered = await client.get(attempt.status_path)
        assert recovered.status_code == 200 and recovered.json() == result
        assert recovered.headers["cache-control"] == "private, no-store"
        assert (await client.post(f"{attempt.path}/cancel", json=body)).json() == result
        for endpoint in ("intent", "complete"):
            denied = await client.post(f"{attempt.path}/{endpoint}", json=body)
            assert denied.status_code == 409
    assert await cancellation_count(db_session, attempt) == 1
    assert await remaining(db_session, attempt.source.stock) == 60
    assert await financial_digest(db_session) == before


async def test_cancelled_uuid_stays_terminal_after_corrected_cart_completes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    attempt = await saved_attempt(client, db_session, units=61)
    old_body = attempt.request.model_dump(mode="json")
    async with cart_actor(db_session):
        assert (await client.post(f"{attempt.path}/cancel", json=old_body)).status_code == 200
        await put_physical(db_session, attempt.source.sale.id, item_id=attempt.item_id, quantity=3)
        corrected = await quoted_request(client, attempt.source.sale.id)
        assert corrected.operation_id != attempt.request.operation_id
        body = corrected.model_dump(mode="json")
        assert (await client.post(f"{attempt.path}/intent", json=body)).status_code == 200
        completed = await client.post(f"{attempt.path}/complete", json=body)
        assert completed.status_code == 200
        assert completed.json()["operation_id"] == str(corrected.operation_id)
        assert await remaining(db_session, attempt.source.stock) == 57
        before = await financial_digest(db_session)
        assert (await client.get(attempt.status_path)).json()["state"] == "cancelled"
        assert (await client.post(f"{attempt.path}/cancel", json=old_body)).json()[
            "state"
        ] == "cancelled"
        assert (await client.post(f"{attempt.path}/complete", json=old_body)).status_code == 409
        assert await financial_digest(db_session) == before


async def test_cancelled_uuid_cannot_complete_after_compatible_stock_is_replenished(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    attempt = await saved_attempt(client, db_session, units=61)
    body = attempt.request.model_dump(mode="json")
    async with cart_actor(db_session):
        assert (await client.post(f"{attempt.path}/cancel", json=body)).status_code == 200
        refill = await priced_source(db_session, quantity=1)
        assert await remaining(db_session, attempt.source.stock) == 60
        assert await remaining(db_session, refill) == 30
        current = await client.get(f"/api/v1/sales/{attempt.source.sale.id}/physical-cart/quote")
        assert current.status_code == 200
        assert current.json()["revision_hash"] == attempt.request.expected_revision_hash
        before = await financial_digest(db_session)
        denied = await client.post(f"{attempt.path}/complete", json=body)
        assert denied.status_code == 409
        assert (await client.get(attempt.status_path)).json()["state"] == "cancelled"
        assert await financial_digest(db_session) == before


async def test_completed_first_returns_original_receipt_instead_of_cancelling(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    attempt = await saved_attempt(client, db_session)
    body = attempt.request.model_dump(mode="json")
    async with cart_actor(db_session):
        original = await client.post(f"{attempt.path}/complete", json=body)
        assert original.status_code == 200
        before = await financial_digest(db_session)
        for _ in range(2):
            response = await client.post(f"{attempt.path}/cancel", json=body)
            assert response.status_code == 200
            assert response.json()["state"] == "completed"
            assert response.json()["result"] == original.json()
        assert (await client.get(attempt.status_path)).json()["state"] == "completed"
    assert await cancellation_count(db_session, attempt) == 0
    assert await financial_digest(db_session) == before


@pytest.mark.parametrize(
    "field", ["cash_tendered", "expected_total_amount", "expected_revision_hash"]
)
@pytest.mark.parametrize("cancelled_first", [False, True])
async def test_cancel_rejects_reused_uuid_with_changed_confirmation(
    client: AsyncClient, db_session: AsyncSession, field: str, cancelled_first: bool
) -> None:
    attempt = await saved_attempt(client, db_session)
    body = attempt.request.model_dump(mode="json")
    async with cart_actor(db_session):
        if cancelled_first:
            assert (await client.post(f"{attempt.path}/cancel", json=body)).status_code == 200
        changed = dict(body)
        changed[field] = (
            "0" * 64
            if field == "expected_revision_hash"
            else str(Decimal(str(body[field])) + Decimal("1.00"))
        )
        before = await financial_digest(db_session)
        denied = await client.post(f"{attempt.path}/cancel", json=changed)
        assert denied.status_code == 409
        assert await financial_digest(db_session) == before
    assert await cancellation_count(db_session, attempt) == int(cancelled_first)


async def test_cancel_requires_saved_intent_and_matching_path(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    attempt = await saved_attempt(client, db_session)
    body = attempt.request.model_dump(mode="json")
    async with cart_actor(db_session):
        unknown = {**body, "operation_id": str(uuid4())}
        assert (await client.post(f"{attempt.path}/cancel", json=unknown)).status_code == 404
        assert (await client.post(f"{cash_path(uuid4())}/cancel", json=body)).status_code == 422
    assert await cancellation_count(db_session, attempt) == 0


@pytest.mark.parametrize("denial", ["permission", "branch", "actor", "tenant"])
async def test_cancel_enforces_current_http_actor_and_resource_scope(
    client: AsyncClient, db_session: AsyncSession, denial: str
) -> None:
    attempt = await saved_attempt(client, db_session)
    async with cart_actor(db_session) as actor:
        if denial == "permission":
            actor.permissions.clear()
        elif denial == "branch":
            actor.permission_scopes["pos.sell"] = frozenset({uuid4()})
        elif denial == "actor":
            actor.user_id = uuid4()
        else:
            actor.tenant_id = uuid4()
        response = await client.post(
            f"{attempt.path}/cancel", json=attempt.request.model_dump(mode="json")
        )
        assert response.status_code == 403
    assert await cancellation_count(db_session, attempt) == 0


@pytest.mark.parametrize("other_tenant", [False, True])
async def test_cancel_history_is_hidden_from_real_other_cashier_or_tenant(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    other_tenant: bool,
) -> None:
    attempt = await saved_attempt(client, db_session)
    # Only seed evidence is committed; every cancellation remains rollback-scoped.
    await db_session.commit()
    context = runtime(db_session)
    stock = context.stock
    if other_tenant:
        async with AsyncSession(db_engine) as support:
            async with support.begin():
                await support.execute(text("SELECT set_config('app.support_session','true',true)"))
                stock = await seed_ledger_context(support)
    other = await seed_receiving_actor(db_engine, maintenance_engine, stock)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    async with cart_actor(db_session):
        assert (
            await client.post(
                f"{attempt.path}/cancel", json=attempt.request.model_dump(mode="json")
            )
        ).status_code == 200
    await set_receiving_context(db_session, stock, other)
    async with cart_actor(db_session) as actor:
        actor.user_id = other
        actor.tenant_id = stock.tenant_id
        actor.permission_scopes["pos.sell"] = None
        expected = 404 if other_tenant else 403
        assert (
            await client.post(
                f"{attempt.path}/cancel", json=attempt.request.model_dump(mode="json")
            )
        ).status_code == expected
        assert (await client.get(attempt.status_path)).status_code == expected
        assert await cancellation_count(db_session, attempt) == 0
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await cancellation_count(db_session, attempt) == 1


async def test_real_support_connection_cannot_cancel_cash_attempt(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    attempt = await saved_attempt(client, db_session)
    context = runtime(db_session)
    async with AsyncSession(db_engine) as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text(
                "SELECT set_config('app.tenant_id',:tenant,true),"
                "set_config('app.user_id',:actor,true)"
            ),
            {"tenant": str(context.stock.tenant_id), "actor": str(context.actor_id)},
        )

        async def override_support_db() -> AsyncIterator[AsyncSession]:
            yield support

        async with cart_actor(db_session):
            app.dependency_overrides[get_db] = override_support_db
            response = await client.post(
                f"{attempt.path}/cancel", json=attempt.request.model_dump(mode="json")
            )
            assert response.status_code == 403
    assert await cancellation_count(db_session, attempt) == 0


async def test_cancellation_is_audited_and_cannot_be_updated_or_deleted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    attempt = await saved_attempt(client, db_session)
    async with cart_actor(db_session):
        assert (
            await client.post(
                f"{attempt.path}/cancel", json=attempt.request.model_dump(mode="json")
            )
        ).status_code == 200
    for command in (
        "UPDATE pos_physical_cash_cancellation SET request_hash=request_hash",
        "DELETE FROM pos_physical_cash_cancellation",
    ):
        with pytest.raises(DBAPIError) as error:
            async with db_session.begin_nested():
                await db_session.execute(
                    text(f"{command} WHERE id=:id"), {"id": attempt.request.operation_id}
                )
        assert getattr(error.value.orig, "sqlstate", None) == "42501"
    audit = list(
        (
            await db_session.execute(
                text(
                    "SELECT new_values FROM audit_log WHERE tenant_id=:tenant "
                    "AND table_name='pos_physical_cash_cancellation'"
                ),
                {"tenant": runtime(db_session).stock.tenant_id},
            )
        ).scalars()
    )
    assert len(audit) == 1
    assert (
        not {"result_payload", "receipt_payload", "quote_payload", "cash_tendered"}
        & audit[0].keys()
    )
    assert await cancellation_count(db_session, attempt) == 1


@pytest.mark.parametrize(
    "field,expected_state",
    [
        ("tenant", "42501"),
        ("cashier", "42501"),
        ("branch", "42501"),
        ("sale", "23514"),
        ("hash", "23514"),
    ],
)
async def test_database_cancellation_guard_rejects_forged_scope_and_hash(
    client: AsyncClient, db_session: AsyncSession, field: str, expected_state: str
) -> None:
    attempt = await saved_attempt(client, db_session)
    forged: object = "0" * 64 if field == "hash" else uuid4()
    before = await financial_digest(db_session)
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await insert_cancellation(db_session, attempt, **{field: forged})
    assert getattr(error.value.orig, "sqlstate", None) == expected_state
    assert await cancellation_count(db_session, attempt) == 0
    assert await financial_digest(db_session) == before


async def test_database_cancellation_stamps_its_real_actor(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    attempt = await saved_attempt(client, db_session)
    await insert_cancellation(db_session, attempt, author=uuid4())
    author = await db_session.scalar(
        text("SELECT created_by FROM pos_physical_cash_cancellation WHERE id=:id"),
        {"id": attempt.request.operation_id},
    )
    assert author == runtime(db_session).actor_id


async def post_stock_before_marker(session: AsyncSession, attempt: Attempt) -> None:
    # Preserve the existing incoming/router bootstrap order during isolated collection.
    from app.domains.inventory.unit_dispensing_v2_repository import UnitDispensingV2Repository
    from app.domains.inventory.unit_dispensing_v2_service import PhysicalUnitLedgerV2Service

    source = attempt.source
    context = runtime(session)
    await PhysicalUnitLedgerV2Service(UnitDispensingV2Repository(session)).dispense_priced_cart(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        command_id=attempt.request.operation_id,
        checkout_sale_id=source.sale.id,
        lines=(
            UnitPricedCartLineV2(
                id=attempt.item_id,
                catalog_id=context.stock.catalog_id,
                packaging=source.stock.packaging,
                sale_format="unit",
                quantity=3,
                discount_amount=Decimal("0.00"),
            ),
        ),
    )


@pytest.mark.parametrize("table", ["pos_physical_checkout", "pos_mixed_checkout"])
async def test_database_marker_fence_rolls_back_preceding_real_stock_posting(
    client: AsyncClient, db_session: AsyncSession, table: str
) -> None:
    attempt = await saved_attempt(client, db_session)
    async with cart_actor(db_session):
        assert (
            await client.post(
                f"{attempt.path}/cancel", json=attempt.request.model_dump(mode="json")
            )
        ).status_code == 200
    before = await financial_digest(db_session)
    with pytest.raises(DBAPIError, match="physical_cash_operation_cancelled") as error:
        async with db_session.begin_nested():
            # The existing stock binding is intentionally unchanged. Even a direct
            # caller that bypasses Python cannot commit without its checkout marker.
            await post_stock_before_marker(db_session, attempt)
            assert await remaining(db_session, attempt.source.stock) == 57
            await db_session.execute(
                text(
                    f"INSERT INTO {table} (tenant_id,id,sale_id,branch_id,register_id,shift_id,"
                    "cashier_user_id,cash_intent_id,stock_command_id,payment_id,request_hash,"
                    "result_payload,receipt_payload,created_by) "
                    "SELECT tenant_id,id,sale_id,branch_id,register_id,shift_id,cashier_user_id,"
                    "id,id,id,request_hash,'{}'::jsonb,'{}'::jsonb,created_by "
                    "FROM pos_physical_cash_cancellation WHERE id=:id"
                ),
                {"id": attempt.request.operation_id},
            )
    assert getattr(error.value.orig, "sqlstate", None) == "23514"
    assert await remaining(db_session, attempt.source.stock) == 60
    assert await financial_digest(db_session) == before
