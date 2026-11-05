"""Physical quarantine reads require real warehouse access and preserve all history."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import BusinessRuleError
from app.domains.customer_returns.physical_repository import PhysicalCustomerReturnsRepository
from app.domains.pos.physical_cash_refund_review import physical_refund_revision
from app.domains.pos.physical_cash_refund_schemas import PhysicalCashRefundResultV3
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.physical_zero_return_repository import PhysicalZeroReturnRepository
from app.domains.pos.physical_zero_return_service import PhysicalZeroReturnService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import LedgerContext, seed_ledger_context
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_physical_cash_checkout import complete
from tests.domains.pos.test_physical_cash_refund import facts, refund
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields
from tests.domains.pos.test_physical_return_journal import receive
from tests.domains.pos.test_physical_zero_return import source as zero_source
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
PATH = "/api/v1/customer-returns/physical"


@asynccontextmanager
async def queue_actor(session: AsyncSession) -> AsyncIterator[CurrentUser]:
    async with cart_actor(session) as actor:
        actor.permissions = {"customer_returns.view"}
        actor.permission_scopes = {
            "customer_returns.view": frozenset({runtime(session).stock.branch_id})
        }
        yield actor


async def cash_source(
    session: AsyncSession, *, quantity: int = 31, returned: int = 31, is_test: bool = False
) -> tuple[PhysicalCheckoutResultV3, PhysicalCashRefundResultV3]:
    source = await checkout_source(session)
    source.sale.is_test = is_test
    await session.flush()
    item = await put_physical(session, source.sale.id, sale_format="unit", quantity=quantity)
    original = await complete(session, await saved_request(session, source.sale))
    result = await refund(session, original.sale_id, item.id, original.shift_id, returned)
    return original, result


async def test_cash_queue_aggregates_same_lot_slices_and_preserves_history(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    original, result = await cash_source(db_session)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_physical_return_slice WHERE command_id=:id"),
            {"id": result.operation_id},
        )
        == 2
    )
    before = await facts(db_session)
    async with queue_actor(db_session):
        response = await client.get(PATH)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    payload = response.json()
    assert payload["total"] == 1 and len(payload["items"]) == 1
    row = payload["items"][0]
    assert row["operation_id"] == str(result.operation_id)
    assert row["line_id"] == str(original.lines[0].id)
    assert row["base_units"] == 31 and row["unit_label"] == "табл."
    assert row["intake_kind"] == "cash_refund" and row["status"] == "pending"
    assert row["parent_receipt_number"] == original.receipt_number
    assert row["return_sale_id"] == str(result.sale_id)
    assert row["return_receipt_number"] == result.receipt.receipt_number
    assert row["catalog_name"] == original.lines[0].name
    assert row["report_timezone"]
    assert "packaging" not in row and "result_payload" not in row
    assert_no_cost_fields(payload)
    assert await facts(db_session) == before


async def test_zero_queue_has_no_refund_sale_and_uses_historical_name(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    original = await zero_source(db_session)
    selections = (PhysicalReturnSelection(original.lines[0].id, "unit", 1),)
    result = await PhysicalZeroReturnService(PhysicalZeroReturnRepository(db_session)).receive(
        tenant_id=original.tenant_id,
        actor_id=original.cashier_id,
        parent_sale_id=original.sale_id,
        operation_id=uuid4(),
        selections=selections,
        expected_revision_hash=physical_refund_revision(original, {}, selections),
    )
    await db_session.execute(
        text("UPDATE tenant_catalog SET brand_name='New live catalog name' WHERE id=:id"),
        {"id": original.lines[0].catalog_id},
    )
    async with queue_actor(db_session):
        response = await client.get(PATH, params={"search": original.lines[0].name})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 1
        row = payload["items"][0]
        assert row["operation_id"] == str(result.operation_id)
        assert row["catalog_name"] == original.lines[0].name
        assert row["base_units"] == 1 and row["intake_kind"] == "zero_return"
        assert row["return_sale_id"] is None and row["return_receipt_number"] is None
        changed_name = await client.get(PATH, params={"search": "New live catalog name"})
        assert changed_name.json()["total"] == 0
    assert_no_cost_fields(payload)


@pytest.mark.parametrize("unpublished_kind", ["prepared_only", "test_sale"])
async def test_queue_excludes_internal_journals_and_rejected_training_documents(
    client: AsyncClient, db_session: AsyncSession, unpublished_kind: str
) -> None:
    if unpublished_kind == "test_sale":
        with pytest.raises(BusinessRuleError, match="Physical test sales"):
            await cash_source(db_session, quantity=1, returned=1, is_test=True)
    else:
        source = await checkout_source(db_session)
        item = await put_physical(db_session, source.sale.id, sale_format="pack")
        await complete(db_session, await saved_request(db_session, source.sale))
        await receive(db_session, source.sale.id, item.id)
    async with queue_actor(db_session):
        response = await client.get(PATH)
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 1, "page_size": 50}


async def test_queue_search_count_and_pagination_use_the_same_scoped_rows(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    original, first = await cash_source(db_session, quantity=3, returned=1)
    second = await refund(db_session, original.sale_id, original.lines[0].id, original.shift_id)
    third = await refund(db_session, original.sale_id, original.lines[0].id, original.shift_id)
    async with queue_actor(db_session) as actor:
        pages = [
            (await client.get(PATH, params={"page": page, "page_size": 1})).json()
            for page in (1, 2, 3, 4)
        ]
        assert all(page["total"] == 3 for page in pages)
        assert pages[3]["items"] == []
        assert {page["items"][0]["operation_id"] for page in pages[:3]} == {
            str(result.operation_id) for result in (first, second, third)
        }
        for term, total in (
            (original.receipt_number, 3),
            (third.receipt.receipt_number, 1),
            ("%", 0),
            ("_", 0),
        ):
            response = await client.get(PATH, params={"search": term})
            assert response.status_code == 200
            assert response.json()["total"] == total
        actor.permission_scopes["customer_returns.view"] = frozenset({uuid4()})
        assert (await client.get(PATH)).json()["total"] == 0
        assert (await client.get(PATH, params={"branch_id": str(original.branch_id)})).json()[
            "items"
        ] == []


async def test_actual_view_only_reader_can_read_projection_but_not_private_journal(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    _, result = await cash_source(db_session)
    context = runtime(db_session)
    await db_session.commit()
    reader = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("customer_returns.view",)
    )
    await set_receiving_context(db_session, context.stock, reader)
    assert await db_session.scalar(text("SELECT count(*) FROM pos_physical_return_command")) == 0
    assert await db_session.scalar(text("SELECT count(*) FROM pos_physical_cash_refund")) == 0
    async with queue_actor(db_session) as actor:
        actor.user_id = reader
        response = await client.get(PATH)
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["operation_id"] == str(result.operation_id)
    assert_no_cost_fields(response.json())


@pytest.mark.parametrize("scope", ["branch", "tenant", "permission"])
async def test_database_scope_is_independent_of_overclaimed_http_permissions(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    scope: str,
) -> None:
    original, _ = await cash_source(db_session, quantity=1, returned=1)
    context = runtime(db_session)
    await db_session.commit()
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            if scope == "tenant":
                other = await seed_ledger_context(support)
            elif scope == "branch":
                branch = await support.scalar(
                    text(
                        "INSERT INTO branch(tenant_id,name) "
                        "VALUES(:tenant,'Queue isolated branch') RETURNING id"
                    ),
                    {"tenant": original.tenant_id},
                )
                assert isinstance(branch, UUID)
                other = LedgerContext(original.tenant_id, branch, context.stock.catalog_id)
            else:
                other = context.stock
    reader = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        other,
        permissions=("pos.refund",) if scope == "permission" else ("customer_returns.view",),
    )
    await set_receiving_context(db_session, other, reader)
    async with queue_actor(db_session) as actor:
        actor.user_id = reader
        actor.permission_scopes["customer_returns.view"] = None
        # Even a request claiming the original tenant and all branches cannot broaden SQL access.
        response = await client.get(PATH, params={"search": original.receipt_number})
    assert response.status_code == 200
    assert response.json()["total"] == 0 and response.json()["items"] == []
    rows, count = await PhysicalCustomerReturnsRepository(db_session).list_items(
        tenant_id=original.tenant_id,
        branch_id=None,
        search=None,
        allowed_branch_ids=None,
        page=1,
        page_size=50,
    )
    assert rows == [] and count == 0


@pytest.mark.parametrize(
    "invalid_context", ["missing_actor", "spoofed_support_flag", "inactive_actor"]
)
async def test_direct_sql_read_requires_real_active_identity(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, invalid_context: str
) -> None:
    original, _ = await cash_source(db_session, quantity=1, returned=1)
    context = runtime(db_session)
    query = text("SELECT count(*) FROM public.physical_customer_return_queue(:tenant)")
    assert await db_session.scalar(query, {"tenant": original.tenant_id}) == 1
    if invalid_context == "missing_actor":
        await db_session.execute(text("SELECT set_config('app.user_id','',true)"))
        assert await db_session.scalar(query, {"tenant": original.tenant_id}) == 0
    elif invalid_context == "spoofed_support_flag":
        await db_session.execute(text("SELECT set_config('app.support_session','true',true)"))
        # A caller-controlled flag cannot change the real database login into
        # support. Existing hardened helpers require actual support authority.
        assert await db_session.scalar(text("SELECT public.is_support_session()")) is False
        assert await db_session.scalar(query, {"tenant": original.tenant_id}) == 1
    else:
        await db_session.commit()
        async with maintenance_engine.begin() as connection:
            assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
            await connection.execute(
                text("UPDATE public.app_user SET status='blocked' WHERE id=:actor"),
                {"actor": context.actor_id},
            )
        try:
            await set_receiving_context(db_session, context.stock, context.actor_id)
            assert await db_session.scalar(query, {"tenant": original.tenant_id}) == 0
        finally:
            async with maintenance_engine.begin() as connection:
                await connection.execute(
                    text("UPDATE public.app_user SET status='active' WHERE id=:actor"),
                    {"actor": context.actor_id},
                )


async def test_queue_requires_permission_and_rejects_invalid_pagination(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    async with queue_actor(db_session) as actor:
        for parameters in (
            {"page": 0},
            {"page": 2147483648},
            {"page_size": 201},
            {"search": "x" * 161},
        ):
            assert (await client.get(PATH, params=parameters)).status_code == 422
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        assert (await client.get(PATH)).status_code == 403
