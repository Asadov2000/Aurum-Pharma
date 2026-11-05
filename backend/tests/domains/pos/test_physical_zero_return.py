"""Zero-value returns quarantine historical units without financial side effects."""

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.foundation.repository import FoundationRepository
from app.domains.pos.physical_cash_refund_review import physical_refund_revision
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository
from app.domains.pos.physical_zero_return_repository import PhysicalZeroReturnRepository
from app.domains.pos.physical_zero_return_schemas import PhysicalZeroReturnResult
from app.domains.pos.physical_zero_return_service import PhysicalZeroReturnService
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_physical_cash_checkout import complete
from tests.domains.pos.test_physical_cash_refund import close_shift, facts
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields, refund_actor
from tests.domains.pos.test_physical_return_journal import receive as unreviewed_receive
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def concurrent_intake(
    context: ReceivingRuntime,
    original: PhysicalCheckoutResultV3,
    operation_id: UUID,
    review: str,
) -> PhysicalZeroReturnResult | ConflictError:
    try:
        async with context.factory.begin() as session:
            await set_receiving_context(session, context.stock, context.actor_id)
            return await PhysicalZeroReturnService(PhysicalZeroReturnRepository(session)).receive(
                tenant_id=original.tenant_id,
                actor_id=original.cashier_id,
                operation_id=operation_id,
                parent_sale_id=original.sale_id,
                selections=(PhysicalReturnSelection(original.lines[0].id, "unit", 1),),
                expected_revision_hash=review,
            )
    except ConflictError as error:
        return error


@pytest.mark.parametrize("same_operation", [True, False])
async def test_concurrent_reviewed_intakes_do_not_receive_the_same_unit_twice(
    db_session: AsyncSession,
    same_operation: bool,
) -> None:
    original = await source(db_session)
    context = runtime(db_session)
    review = physical_refund_revision(
        original,
        {},
        (PhysicalReturnSelection(original.lines[0].id, "unit", 1),),
    )
    await db_session.commit()
    operation_id = uuid4()
    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                concurrent_intake(
                    context, original, operation_id if same_operation else uuid4(), review
                )
                for _ in range(3)
            )
        ),
        timeout=15,
    )
    successful = [result for result in results if isinstance(result, PhysicalZeroReturnResult)]
    assert len(successful) == (3 if same_operation else 1)
    assert all(result == successful[0] for result in successful)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await db_session.scalar(text("SELECT count(*) FROM pos_physical_zero_return")) == 1
    assert (
        await db_session.scalar(text("SELECT sum(base_units) FROM pos_physical_return_line")) == 1
    )


async def source(session: AsyncSession, *, positive: bool = False) -> PhysicalCheckoutResultV3:
    prepared = await checkout_source(session)
    await put_physical(
        session,
        prepared.sale.id,
        sale_format="pack",
        discount=Decimal("0.00" if positive else "59.99"),
    )
    original = await complete(session, await saved_request(session, prepared.sale))
    settings = await FoundationRepository(session).get_settings(original.tenant_id)
    assert settings is not None
    await FoundationRepository(session).update_settings(settings, refund_reason_mode="off")
    return original


async def body(session: AsyncSession, original: PhysicalCheckoutResultV3) -> dict[str, object]:
    selections = (PhysicalReturnSelection(original.lines[0].id, "unit", 1),)
    cursors = await PhysicalReturnJournalRepository(session).cursors(
        original.tenant_id, original.stock_command_id
    )
    return {
        "operation_id": str(uuid4()),
        "items": [{"line_id": str(original.lines[0].id), "sale_format": "unit", "quantity": 1}],
        "expected_revision_hash": physical_refund_revision(original, cursors, selections),
    }


def path(original: PhysicalCheckoutResultV3) -> str:
    return f"/api/v1/sales/{original.sale_id}/physical-zero-returns"


async def all_facts(session: AsyncSession, tenant_id: UUID) -> dict[str, list[object]]:
    result = await facts(session)
    result["pos_physical_zero_return"] = list(
        (
            await session.execute(
                text(
                    "SELECT to_jsonb(row) FROM pos_physical_zero_return row "
                    "WHERE tenant_id=:tenant ORDER BY id"
                ),
                {"tenant": tenant_id},
            )
        ).scalars()
    )
    return result


async def test_intake_without_open_shift_replays_and_recovers_without_cash_or_stock_changes(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    original = await source(db_session)
    await close_shift(db_session, original.shift_id)
    before = await all_facts(db_session, original.tenant_id)
    async with refund_actor(db_session):
        request = await body(db_session, original)
        quote = await client.post(
            f"/api/v1/sales/{original.sale_id}/physical-cash-refunds/quote",
            json={"items": request["items"]},
        )
        assert quote.status_code == 200 and quote.json()["total_amount"] == "0.00"
        response = await client.post(f"{path(original)}/complete", json=request)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        acknowledgment = response.json()
        assert acknowledgment["state"] == "prepared_quarantine"
        assert acknowledgment["total_amount"] == "0.00"
        assert acknowledgment["lines"] == [
            {"line_id": str(original.lines[0].id), "base_units": 1, "offset": 0}
        ]
        assert "sale_id" not in acknowledgment and "receipt" not in acknowledgment
        assert_no_cost_fields(acknowledgment)
        await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        after = await all_facts(db_session, original.tenant_id)
        for table in before:
            if not table.startswith("pos_physical_return_") and table != "pos_physical_zero_return":
                assert after[table] == before[table], table
        assert len(after["pos_physical_zero_return"]) == 1
        settings = await FoundationRepository(db_session).get_settings(original.tenant_id)
        assert settings is not None
        await FoundationRepository(db_session).update_settings(
            settings, refund_reason_mode="required_with_text"
        )
        assert (
            await client.post(f"{path(original)}/complete", json=request)
        ).json() == acknowledgment
        status = await client.get(f"{path(original)}/operations/{request['operation_id']}")
        assert status.status_code == 200 and status.json() == acknowledgment
        assert status.headers["cache-control"] == "private, no-store"
        assert await all_facts(db_session, original.tenant_id) == after


@pytest.mark.parametrize("invalid", ["positive", "stale", "reason", "cash_fields"])
async def test_denials_roll_back_every_quarantine_row(
    client: AsyncClient,
    db_session: AsyncSession,
    invalid: str,
) -> None:
    original = await source(db_session, positive=invalid == "positive")
    request = await body(db_session, original)
    if invalid == "stale":
        request["expected_revision_hash"] = "0" * 64
    elif invalid == "reason":
        settings = await FoundationRepository(db_session).get_settings(original.tenant_id)
        assert settings is not None
        await FoundationRepository(db_session).update_settings(
            settings, refund_reason_mode="required"
        )
    elif invalid == "cash_fields":
        request["expected_total_amount"] = "0.00"
        request["shift_id"] = str(original.shift_id)
    before = await all_facts(db_session, original.tenant_id)
    async with refund_actor(db_session):
        response = await client.post(f"{path(original)}/complete", json=request)
    assert response.status_code == (409 if invalid == "stale" else 422)
    assert await all_facts(db_session, original.tenant_id) == before


async def test_cursor_changes_and_changed_replay_requests_are_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    original = await source(db_session)
    async with refund_actor(db_session):
        first = await body(db_session, original)
        stale = await body(db_session, original)
        assert (await client.post(f"{path(original)}/complete", json=first)).status_code == 200
        before = await all_facts(db_session, original.tenant_id)
        assert (await client.post(f"{path(original)}/complete", json=stale)).status_code == 409
        changed = {**first, "expected_revision_hash": "0" * 64}
        assert (await client.post(f"{path(original)}/complete", json=changed)).status_code == 409
        assert await all_facts(db_session, original.tenant_id) == before
        fresh = await body(db_session, original)
        response = await client.post(f"{path(original)}/complete", json=fresh)
        assert response.status_code == 200 and response.json()["lines"][0]["offset"] == 1


async def test_existing_unreviewed_journal_cannot_be_adopted_as_zero_intake(
    db_session: AsyncSession,
) -> None:
    original = await source(db_session)
    operation_id = uuid4()
    await unreviewed_receive(
        db_session, original.sale_id, original.lines[0].id, operation_id=operation_id
    )
    before = await all_facts(db_session, original.tenant_id)
    service = PhysicalZeroReturnService(PhysicalZeroReturnRepository(db_session))
    with pytest.raises(ConflictError, match="already exists"):
        await service.receive(
            tenant_id=original.tenant_id,
            actor_id=original.cashier_id,
            operation_id=operation_id,
            parent_sale_id=original.sale_id,
            selections=(PhysicalReturnSelection(original.lines[0].id, "unit", 1),),
            expected_revision_hash="0" * 64,
        )
    assert await all_facts(db_session, original.tenant_id) == before


async def test_recovery_denies_other_branch_and_reports_unknown_operation(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    original = await source(db_session)
    async with refund_actor(db_session) as actor:
        request = await body(db_session, original)
        assert (await client.post(f"{path(original)}/complete", json=request)).status_code == 200
        actor.permission_scopes = {"pos.refund": frozenset({uuid4()})}
        assert (
            await client.get(f"{path(original)}/operations/{request['operation_id']}")
        ).status_code == 403
        actor.permission_scopes = {"pos.refund": frozenset({original.branch_id})}
        unknown = await client.get(f"{path(original)}/operations/{uuid4()}")
        assert unknown.status_code == 200 and unknown.json()["state"] == "not_found"


async def test_typed_service_rejects_invalid_review_before_any_write(
    db_session: AsyncSession,
) -> None:
    original = await source(db_session)
    before = await all_facts(db_session, original.tenant_id)
    with pytest.raises(BusinessRuleError, match="review hash"):
        await PhysicalZeroReturnService(PhysicalZeroReturnRepository(db_session)).receive(
            tenant_id=original.tenant_id,
            actor_id=original.cashier_id,
            operation_id=uuid4(),
            parent_sale_id=original.sale_id,
            selections=(PhysicalReturnSelection(original.lines[0].id, "unit", 1),),
            expected_revision_hash="invalid",
        )
    assert await all_facts(db_session, original.tenant_id) == before


async def test_other_authorized_actor_cannot_recover_known_intake(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    original = await source(db_session)
    context = runtime(db_session)
    async with refund_actor(db_session):
        request = await body(db_session, original)
        assert (await client.post(f"{path(original)}/complete", json=request)).status_code == 200
    await db_session.commit()
    other = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        context.stock,
        permissions=("pos.refund",),
    )
    await set_receiving_context(db_session, context.stock, other)
    async with refund_actor(db_session) as claimed:
        claimed.user_id = other
        response = await client.get(f"{path(original)}/operations/{request['operation_id']}")
        assert response.status_code == 200
        assert response.json() == {
            "state": "not_found",
            "operation_id": request["operation_id"],
            "parent_sale_id": str(original.sale_id),
        }
