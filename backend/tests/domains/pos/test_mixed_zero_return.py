"""Reviewed mixed quarantine intake never pays cash or restocks saleable goods."""

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AurumError, BusinessRuleError, ConflictError, NotFoundError
from app.domains.inventory.repository import InventoryRepository
from app.domains.inventory.unit_stock_v2 import SaleFormatV2
from app.domains.pos.mixed_cash_refund_review import mixed_refund_revision
from app.domains.pos.mixed_return_allocation import MixedReturnSelection
from app.domains.pos.mixed_return_journal_repository import MixedReturnJournalRepository
from app.domains.pos.mixed_zero_return_models import MixedZeroReturn
from app.domains.pos.mixed_zero_return_repository import MixedZeroReturnRepository
from app.domains.pos.mixed_zero_return_schemas import MixedZeroReturnResult
from app.domains.pos.mixed_zero_return_service import MixedZeroReturnService
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.repository import POSRepository
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos.test_mixed_cash_checkout import complete
from tests.domains.pos.test_mixed_cash_refund import facts as refund_facts
from tests.domains.pos.test_mixed_cash_refund import policy, refund
from tests.domains.pos.test_mixed_physical_checkout_preparation import legacy_with_stock
from tests.domains.pos.test_mixed_return_journal import MixedSource
from tests.domains.pos.test_mixed_return_journal import receive as unreviewed_receive
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields, refund_actor
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def source(session: AsyncSession, *, positive: bool = False) -> MixedSource:
    prepared = await checkout_source(session)
    legacy = await legacy_with_stock(session, prepared.sale)
    await InventoryRepository(session).insert_movement(
        tenant_id=prepared.sale.tenant_id,
        batch_id=legacy.batch_id,
        movement_type="incoming",
        qty_delta=Decimal("0.002"),
    )
    await POSRepository(session).update_item(
        legacy,
        qty=Decimal("0.003"),
        discount_amount=Decimal("0.07"),
        total_price=Decimal("0.01"),
    )
    await POSRepository(session).update_sale(prepared.sale, total_amount=Decimal("0.01"))
    physical = await put_physical(
        session,
        prepared.sale.id,
        position=2,
        sale_format="pack",
        quantity=2,
        discount=Decimal("0.01" if positive else "119.99"),
    )
    original = await complete(session, await saved_request(session, prepared.sale))
    await policy(session, original.tenant_id, "off")
    return MixedSource(original, legacy.id, physical.id, legacy.batch_id)


async def review(
    session: AsyncSession, original: MixedSource, selections: tuple[MixedReturnSelection, ...]
) -> str:
    legacy, physical = await MixedReturnJournalRepository(session).cursors(
        original.original.tenant_id, original.original.sale_id
    )
    return mixed_refund_revision(original.original, legacy, physical, selections)


async def intake(
    session: AsyncSession,
    original: MixedSource,
    *,
    operation_id: UUID | None = None,
    selections: tuple[MixedReturnSelection, ...] | None = None,
    revision: str | None = None,
) -> MixedZeroReturnResult:
    chosen = original.selections() if selections is None else selections
    return await MixedZeroReturnService(MixedZeroReturnRepository(session)).receive(
        tenant_id=original.original.tenant_id,
        actor_id=runtime(session).actor_id,
        parent_sale_id=original.original.sale_id,
        operation_id=operation_id or uuid4(),
        selections=chosen,
        expected_revision_hash=revision or await review(session, original, chosen),
    )


async def facts(session: AsyncSession) -> dict[str, list[object]]:
    result = await refund_facts(session)
    result["pos_mixed_zero_return"] = list(
        (
            await session.execute(
                text(
                    "SELECT to_jsonb(row) FROM pos_mixed_zero_return row "
                    "WHERE tenant_id=:tenant ORDER BY id"
                ),
                {"tenant": runtime(session).stock.tenant_id},
            )
        ).scalars()
    )
    return result


def path(original: MixedSource) -> str:
    return f"/api/v1/sales/{original.original.sale_id}/mixed-zero-returns"


async def body(
    session: AsyncSession, original: MixedSource, sale_format: SaleFormatV2 = "unit"
) -> dict[str, object]:
    selections = (
        original.selections()[0],
        PhysicalReturnSelection(original.physical_id, sale_format, 1),
    )
    return {
        "operation_id": str(uuid4()),
        "expected_revision_hash": await review(session, original, selections),
        "items": [
            {
                "inventory_kind": "legacy_pack",
                "line_id": str(original.legacy_id),
                "quantity": "0.001",
            },
            {
                "inventory_kind": "physical_unit",
                "line_id": str(original.physical_id),
                "sale_format": sale_format,
                "quantity": 1,
            },
        ],
    }


@pytest.mark.parametrize("sale_format,units", [("unit", 1), ("blister", 10), ("pack", 30)])
async def test_zero_intake_closed_shift_exact_replay_and_read_have_no_financial_side_effects(
    client: AsyncClient, db_session: AsyncSession, sale_format: SaleFormatV2, units: int
) -> None:
    original = await source(db_session)
    await close_shift(db_session, original.original.shift_id)
    before = await facts(db_session)
    async with refund_actor(db_session):
        request = await body(db_session, original, sale_format)
        response = await client.post(f"{path(original)}/complete", json=request)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        payload = response.json()
        assert payload["kind"] == "mixed_zero_return"
        assert payload["state"] == "prepared_quarantine"
        assert payload["total_amount"] == "0.00"
        assert payload["lines"] == [
            {
                "inventory_kind": "legacy_pack",
                "line_id": str(original.legacy_id),
                "quantity": "0.001",
                "offset": "0.000",
            },
            {
                "inventory_kind": "physical_unit",
                "line_id": str(original.physical_id),
                "quantity": 1,
                "sale_format": sale_format,
                "base_units": units,
                "offset": 0,
            },
        ]
        assert not {"sale_id", "receipt", "payment_id", "shift_id"}.intersection(payload)
        assert_no_cost_fields(payload)
        assert (
            MixedZeroReturnResult.model_validate_json(response.text).model_dump(mode="json")
            == payload
        )
        await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        after = await facts(db_session)
        for table in before:
            if not table.startswith("pos_mixed_return_") and table != "pos_mixed_zero_return":
                assert before[table] == after[table], table
        await policy(db_session, original.original.tenant_id, "required_with_text")
        assert (await client.post(f"{path(original)}/complete", json=request)).json() == payload
        recovered = await client.get(f"{path(original)}/operations/{request['operation_id']}")
        assert recovered.status_code == 200 and recovered.json() == payload
        assert recovered.headers["cache-control"] == "private, no-store"
        assert await facts(db_session) == after


@pytest.mark.parametrize("failure", ["positive", "stale", "reason", "comment", "cash_fields"])
async def test_denials_leave_every_table_unchanged(
    client: AsyncClient, db_session: AsyncSession, failure: str
) -> None:
    original = await source(db_session, positive=failure == "positive")
    request = await body(db_session, original)
    if failure == "stale":
        request["expected_revision_hash"] = "0" * 64
    elif failure in {"reason", "comment"}:
        await policy(db_session, original.original.tenant_id, "required_with_text")
        if failure == "comment":
            request["reason_code"] = "other"
    elif failure == "cash_fields":
        request["shift_id"] = str(original.original.shift_id)
        request["expected_total_amount"] = "0.00"
    before = await facts(db_session)
    async with refund_actor(db_session):
        response = await client.post(f"{path(original)}/complete", json=request)
    assert response.status_code == (409 if failure == "stale" else 422), response.text
    assert await facts(db_session) == before


async def test_cursor_changes_conflicting_uuid_and_final_legacy_cent_are_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    original = await source(db_session)
    async with refund_actor(db_session):
        request = await body(db_session, original)
        stale = await body(db_session, original)
        assert (await client.post(f"{path(original)}/complete", json=request)).status_code == 200
        before = await facts(db_session)
        assert (await client.post(f"{path(original)}/complete", json=stale)).status_code == 409
        changed = {**request, "expected_revision_hash": "0" * 64}
        assert (await client.post(f"{path(original)}/complete", json=changed)).status_code == 409
        assert await facts(db_session) == before
        fresh = await body(db_session, original)
        second = await client.post(f"{path(original)}/complete", json=fresh)
        assert second.status_code == 200
        assert second.json()["lines"][0]["offset"] == "0.001"
        last = await body(db_session, original)
        before = await facts(db_session)
        assert (await client.post(f"{path(original)}/complete", json=last)).status_code == 422
        assert await facts(db_session) == before


@pytest.mark.parametrize("kind", ["unreviewed", "cash"])
async def test_existing_journal_cannot_be_adopted(db_session: AsyncSession, kind: str) -> None:
    original = await source(db_session, positive=kind == "cash")
    command = (
        await refund(db_session, original)
        if kind == "cash"
        else await unreviewed_receive(db_session, original)
    )
    before = await facts(db_session)
    with pytest.raises(ConflictError, match="already exists"):
        await intake(db_session, original, operation_id=command.operation_id)
    assert await facts(db_session) == before


@pytest.mark.parametrize("mode", ["optional", "required", "required_with_text"])
async def test_current_reason_policy_and_trimmed_comment_are_bound_to_replay(
    client: AsyncClient, db_session: AsyncSession, mode: str
) -> None:
    original = await source(db_session)
    await policy(db_session, original.original.tenant_id, mode)
    async with refund_actor(db_session):
        request = await body(db_session, original)
        request.update(reason_code="other", comment="  Причина возврата  ")
        response = await client.post(f"{path(original)}/complete", json=request)
        assert response.status_code == 200, response.text
        before = await facts(db_session)
        changed = {**request, "comment": "Другая причина"}
        assert (await client.post(f"{path(original)}/complete", json=changed)).status_code == 409
        assert await facts(db_session) == before
        saved = await MixedZeroReturnRepository(db_session).get(
            original.original.tenant_id, UUID(str(request["operation_id"]))
        )
        assert saved is not None and saved.comment == "Причина возврата"


async def test_unknown_operation_is_readonly_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    original = await source(db_session)
    operation_id = uuid4()
    before = await facts(db_session)
    async with refund_actor(db_session):
        response = await client.get(f"{path(original)}/operations/{operation_id}")
    assert response.status_code == 200
    assert response.json() == {
        "state": "not_found",
        "operation_id": str(operation_id),
        "parent_sale_id": str(original.original.sale_id),
    }
    assert await facts(db_session) == before


async def test_invalid_review_is_rejected_before_any_write(db_session: AsyncSession) -> None:
    original = await source(db_session)
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError, match="review hash"):
        await intake(db_session, original, revision="INVALID")
    assert await facts(db_session) == before


class CorruptReadRepository(MixedZeroReturnRepository):
    def __init__(self, session: AsyncSession, marker: MixedZeroReturn, corruption: str) -> None:
        super().__init__(session)
        # A detached clone simulates corrupt storage without mutating immutable DB rows.
        self.marker = MixedZeroReturn(
            tenant_id=marker.tenant_id,
            id=marker.id,
            parent_sale_id=marker.parent_sale_id,
            branch_id=marker.branch_id,
            actor_id=marker.actor_id,
            request_hash=marker.request_hash,
            review_hash=marker.review_hash,
            reason_code=marker.reason_code,
            comment=marker.comment,
            result_payload={**marker.result_payload},
        )
        if corruption == "hash":
            self.marker.request_hash = "0" * 64
        elif corruption == "branch":
            self.marker.branch_id = uuid4()
        elif corruption == "cost":
            self.marker.result_payload["physical_cost_amount"] = "0.00"
        elif corruption == "line":
            self.marker.result_payload["lines"] = []
        elif corruption == "review":
            self.marker.review_hash = "f" * 64

    async def get(self, tenant_id: UUID, operation_id: UUID) -> MixedZeroReturn | None:
        assert tenant_id == self.marker.tenant_id and operation_id == self.marker.id
        return self.marker


@pytest.mark.parametrize("corruption", ["hash", "branch", "cost", "line", "review"])
async def test_recovery_fails_closed_on_inconsistent_marker_without_cost_exposure(
    db_session: AsyncSession, corruption: str
) -> None:
    original = await source(db_session)
    result = await intake(db_session, original)
    marker = await MixedZeroReturnRepository(db_session).get(result.tenant_id, result.operation_id)
    assert marker is not None
    before = await facts(db_session)
    with pytest.raises(AurumError, match="Stored mixed zero-value return is inconsistent"):
        await MixedZeroReturnService(CorruptReadRepository(db_session, marker, corruption)).read(
            tenant_id=result.tenant_id,
            actor_id=result.actor_id,
            parent_sale_id=result.parent_sale_id,
            operation_id=result.operation_id,
        )
    assert await facts(db_session) == before


@pytest.mark.parametrize("known", [False, True])
@pytest.mark.parametrize("failure", ["permission", "branch", "claimed_actor", "claimed_tenant"])
async def test_current_scope_checked_before_result_or_not_found(
    client: AsyncClient, db_session: AsyncSession, known: bool, failure: str
) -> None:
    original = await source(db_session)
    result = await intake(db_session, original)
    before = await facts(db_session)
    async with refund_actor(db_session) as actor:
        request = await body(db_session, original)
        if failure == "permission":
            actor.permissions = {"sales.view.tenant"}
        elif failure == "branch":
            actor.permission_scopes = {"pos.refund": frozenset({uuid4()})}
        elif failure == "claimed_actor":
            actor.user_id = uuid4()
        else:
            actor.tenant_id = uuid4()
        operation_id = result.operation_id if known else uuid4()
        assert (await client.get(f"{path(original)}/operations/{operation_id}")).status_code == 403
        assert (await client.post(f"{path(original)}/complete", json=request)).status_code == 403
    assert await facts(db_session) == before


async def test_readonly_tenant_recovers_without_writes(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    original = await source(db_session)
    result = await intake(db_session, original)
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"),
            {"id": original.original.tenant_id},
        )
    context = runtime(db_session)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await facts(db_session)
    async with refund_actor(db_session):
        response = await client.get(f"{path(original)}/operations/{result.operation_id}")
        assert response.status_code == 200 and response.json() == result.model_dump(mode="json")
        request = await body(db_session, original)
        denied = await client.post(f"{path(original)}/complete", json=request)
        assert denied.status_code == 422
        assert denied.json()["error"]["details"] == {"status": "readonly"}
    assert await facts(db_session) == before


async def test_other_authorized_cashier_cannot_recover_saved_intake(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    original = await source(db_session)
    result = await intake(db_session, original)
    context = runtime(db_session)
    await db_session.commit()
    other = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, other)
    async with refund_actor(db_session) as actor:
        actor.user_id = other
        response = await client.get(f"{path(original)}/operations/{result.operation_id}")
        assert response.status_code == 200
        assert response.json()["state"] == "not_found"
    await set_receiving_context(db_session, context.stock, context.actor_id)


async def test_real_foreign_tenant_cannot_recover_saved_intake(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    original = await source(db_session)
    result = await intake(db_session, original)
    context = runtime(db_session)
    before = await facts(db_session)
    await db_session.commit()
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            foreign = await seed_ledger_context(support)
    actor_id = await seed_receiving_actor(
        db_engine, maintenance_engine, foreign, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, foreign, actor_id)
    with pytest.raises(NotFoundError):
        await MixedZeroReturnService(MixedZeroReturnRepository(db_session)).read(
            tenant_id=foreign.tenant_id,
            actor_id=actor_id,
            parent_sale_id=result.parent_sale_id,
            operation_id=result.operation_id,
            allowed_branch_ids={foreign.branch_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before


async def concurrent_intake(
    context: ReceivingRuntime, original: MixedSource, operation_id: UUID, revision: str
) -> MixedZeroReturnResult | ConflictError:
    try:
        async with context.factory.begin() as session:
            await set_receiving_context(session, context.stock, context.actor_id)
            return await MixedZeroReturnService(MixedZeroReturnRepository(session)).receive(
                tenant_id=context.stock.tenant_id,
                actor_id=context.actor_id,
                operation_id=operation_id,
                parent_sale_id=original.original.sale_id,
                selections=original.selections(),
                expected_revision_hash=revision,
            )
    except ConflictError as error:
        return error


@pytest.mark.parametrize("same_uuid", [False, True])
async def test_concurrent_intakes_serialize_review_and_uuid(
    db_session: AsyncSession, same_uuid: bool
) -> None:
    original = await source(db_session)
    context = runtime(db_session)
    revision = await review(db_session, original, original.selections())
    operation_id = uuid4()
    await db_session.commit()
    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                concurrent_intake(
                    context, original, operation_id if same_uuid else uuid4(), revision
                )
                for _ in range(3)
            )
        ),
        timeout=15,
    )
    successful = [result for result in results if isinstance(result, MixedZeroReturnResult)]
    assert len(successful) == (3 if same_uuid else 1)
    assert all(result == successful[0] for result in successful)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await db_session.scalar(text("SELECT count(*) FROM pos_mixed_zero_return")) == 1
