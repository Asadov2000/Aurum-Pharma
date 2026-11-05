"""Confirmed mixed intake reads preserve histories and both quantity semantics."""

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import BusinessRuleError
from app.domains.customer_returns.mixed_repository import MixedCustomerReturnsRepository
from app.domains.customer_returns.mixed_schemas import (
    MixedCustomerReturnList,
    MixedCustomerReturnRead,
)
from app.domains.pos.mixed_cash_refund_schemas import MixedCashRefundResultV4
from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from tests.domains.customer_returns.test_physical_queue import queue_actor
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import LedgerContext, seed_ledger_context
from tests.domains.pos.test_mixed_cash_checkout import complete
from tests.domains.pos.test_mixed_cash_refund import refund
from tests.domains.pos.test_mixed_physical_checkout_preparation import legacy_with_stock
from tests.domains.pos.test_mixed_return_journal import mixed_source, receive
from tests.domains.pos.test_mixed_zero_return import facts, intake
from tests.domains.pos.test_mixed_zero_return import source as zero_source
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
PATH = "/api/v1/customer-returns/mixed"


async def cash_source(
    session: AsyncSession, *, returned: int = 31
) -> tuple[MixedCheckoutResultV4, MixedCashRefundResultV4]:
    original = await mixed_source(
        session, sale_format="unit", physical_quantity=31, fractional_legacy=True
    )
    result = await refund(session, original, original.selections("0.001", returned))
    return original.original, result


async def test_cash_queue_preserves_decimal_packs_and_groups_physical_slices_per_lot(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    original, result = await cash_source(db_session)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_mixed_return_slice WHERE command_id=:id"),
            {"id": result.operation_id},
        )
        == 2
    )
    assert (
        await db_session.scalar(
            text("SELECT count(DISTINCT lot_id) FROM pos_mixed_return_slice WHERE command_id=:id"),
            {"id": result.operation_id},
        )
        == 1
    )
    before = await facts(db_session)
    async with queue_actor(db_session):
        response = await client.get(PATH)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    payload = response.json()
    assert payload["total"] == 2 and len(payload["items"]) == 2
    assert MixedCustomerReturnList.model_validate(payload).model_dump(mode="json") == payload
    by_kind = {row["inventory_kind"]: row for row in payload["items"]}
    legacy, physical = by_kind["legacy_pack"], by_kind["physical_unit"]
    legacy_original = original.lines[0]
    assert legacy_original.inventory_kind == "legacy_pack"
    assert legacy["line_id"] == str(legacy_original.id)
    assert legacy["batch_id"] == str(legacy_original.batch_id)
    assert legacy["legacy_quantity"] == "0.001" and legacy["unit_label"] == "уп."
    assert legacy["base_units"] is None and legacy["lot_id"] is None
    assert physical["line_id"] == str(original.lines[1].id)
    assert physical["base_units"] == 31 and physical["unit_label"] == "табл."
    assert physical["legacy_quantity"] is None and physical["batch_id"] is None
    assert physical["lot_id"]
    for patch in (
        {"legacy_quantity": None},
        {"legacy_quantity": "0.0001"},
        {"lot_id": str(uuid4())},
        {"base_units": 1},
        {"unit_label": "табл."},
        {"cost_amount": "1.00"},
        {"return_sale_id": None},
        {"intake_kind": "zero_return"},
    ):
        with pytest.raises(ValidationError):
            MixedCustomerReturnRead.model_validate({**legacy, **patch})
    for patch in ({"lot_id": None}, {"base_units": 1.0}, {"legacy_quantity": "0.001"}):
        with pytest.raises(ValidationError):
            MixedCustomerReturnRead.model_validate({**physical, **patch})
    for row in payload["items"]:
        assert row["operation_id"] == str(result.operation_id)
        assert row["intake_kind"] == "cash_refund" and row["status"] == "pending"
        assert row["parent_receipt_number"] == original.receipt_number
        assert row["return_sale_id"] == str(result.sale_id)
        assert row["return_receipt_number"] == result.receipt.receipt_number
        assert row["report_timezone"]
        assert "packaging" not in row and "result_payload" not in row
    assert_no_cost_fields(payload)
    assert await facts(db_session) == before


async def test_zero_queue_keeps_historical_names_quantities_and_no_financial_document(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    original = await zero_source(db_session)
    result = await intake(db_session, original)
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET brand_name='New live name' "
            "WHERE id=ANY(CAST(:ids AS UUID[]))"
        ),
        {"ids": [line.catalog_id for line in original.original.lines]},
    )
    before = await facts(db_session)
    async with queue_actor(db_session):
        response = await client.get(PATH)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 2
        historical = {str(line.id): line for line in original.original.lines}
        for row in payload["items"]:
            assert row["operation_id"] == str(result.operation_id)
            assert row["catalog_name"] == historical[row["line_id"]].name
            assert row["intake_kind"] == "zero_return"
            assert row["return_sale_id"] is None and row["return_receipt_number"] is None
        assert (await client.get(PATH, params={"search": "New live name"})).json()["total"] == 0
        for line in original.original.lines:
            found = await client.get(PATH, params={"search": line.name})
            assert found.status_code == 200 and found.json()["total"] >= 1
    assert_no_cost_fields(payload)
    assert await facts(db_session) == before


@pytest.mark.parametrize("unpublished", ["prepared_only", "test_sale"])
async def test_queue_excludes_unconfirmed_journals_and_test_sales(
    client: AsyncClient, db_session: AsyncSession, unpublished: str
) -> None:
    if unpublished == "prepared_only":
        await receive(db_session, await mixed_source(db_session))
    else:
        prepared = await checkout_source(db_session)
        await legacy_with_stock(db_session, prepared.sale)
        await put_physical(db_session, prepared.sale.id, position=2)
        request = await saved_request(db_session, prepared.sale)
        prepared.sale.is_test = True
        await db_session.flush()
        with pytest.raises(BusinessRuleError, match="Mixed test sales"):
            await complete(db_session, request)
    async with queue_actor(db_session):
        response = await client.get(PATH)
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 1, "page_size": 50}


async def test_search_count_and_pagination_use_identical_scoped_grouped_rows(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await mixed_source(db_session, fractional_legacy=True)
    results = [await refund(db_session, source) for _ in range(3)]
    before = await facts(db_session)
    async with queue_actor(db_session) as actor:
        pages = [
            (await client.get(PATH, params={"page": page, "page_size": 1})).json()
            for page in range(1, 8)
        ]
        assert all(page["total"] == 6 for page in pages)
        assert pages[-1]["items"] == []
        assert (
            len(
                {
                    (page["items"][0]["operation_id"], page["items"][0]["line_id"])
                    for page in pages[:-1]
                }
            )
            == 6
        )
        for term, total in (
            (source.original.receipt_number, 6),
            (results[-1].receipt.receipt_number, 2),
            ("%", 0),
            ("_", 0),
        ):
            response = await client.get(PATH, params={"search": term})
            assert response.status_code == 200 and response.json()["total"] == total
        actor.permission_scopes["customer_returns.view"] = frozenset({uuid4()})
        assert (await client.get(PATH)).json()["total"] == 0
        filtered = await client.get(PATH, params={"branch_id": str(source.original.branch_id)})
        assert filtered.json()["items"] == []
    assert await facts(db_session) == before


async def test_actual_view_only_reader_can_use_projection_without_private_pos_access(
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
    for table in ("pos_mixed_return_command", "pos_mixed_cash_refund", "pos_mixed_zero_return"):
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    async with queue_actor(db_session) as actor:
        actor.user_id = reader
        response = await client.get(PATH)
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 2
    assert {row["operation_id"] for row in response.json()["items"]} == {str(result.operation_id)}
    assert_no_cost_fields(response.json())


@pytest.mark.parametrize("scope", ["branch", "tenant", "permission"])
async def test_actual_database_scope_cannot_be_broadened_by_http_claims_or_sql_arguments(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    scope: str,
) -> None:
    original, _ = await cash_source(db_session)
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
                        "INSERT INTO branch(tenant_id,name) VALUES(:tenant,'Mixed queue branch') "
                        "RETURNING id"
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
        response = await client.get(PATH)
    assert response.status_code == 200
    assert response.json()["items"] == [] and response.json()["total"] == 0
    rows, count = await MixedCustomerReturnsRepository(db_session).list_items(
        tenant_id=original.tenant_id,
        branch_id=None,
        search=None,
        allowed_branch_ids=None,
        page=1,
        page_size=50,
    )
    assert rows == [] and count == 0


@pytest.mark.parametrize("invalid", ["missing_actor", "spoofed_support_flag", "inactive_actor"])
async def test_direct_sql_requires_a_real_active_actor(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, invalid: str
) -> None:
    original, _ = await cash_source(db_session)
    context = runtime(db_session)
    query = text("SELECT count(*) FROM public.mixed_customer_return_queue(:tenant)")
    assert await db_session.scalar(query, {"tenant": original.tenant_id}) == 2
    if invalid == "missing_actor":
        await db_session.execute(text("SELECT set_config('app.user_id','',true)"))
        assert await db_session.scalar(query, {"tenant": original.tenant_id}) == 0
    elif invalid == "spoofed_support_flag":
        await db_session.execute(text("SELECT set_config('app.support_session','true',true)"))
        assert await db_session.scalar(text("SELECT public.is_support_session()")) is False
        assert await db_session.scalar(query, {"tenant": original.tenant_id}) == 2
    else:
        await db_session.commit()
        async with maintenance_engine.begin() as connection:
            assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
            await connection.execute(
                text("UPDATE app_user SET status='blocked' WHERE id=:actor"),
                {"actor": context.actor_id},
            )
        try:
            await set_receiving_context(db_session, context.stock, context.actor_id)
            assert await db_session.scalar(query, {"tenant": original.tenant_id}) == 0
        finally:
            async with maintenance_engine.begin() as connection:
                await connection.execute(
                    text("UPDATE app_user SET status='active' WHERE id=:actor"),
                    {"actor": context.actor_id},
                )


async def test_queue_validates_filters_permission_and_has_no_write_endpoint(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    original, _ = await cash_source(db_session)
    async with queue_actor(db_session) as actor:
        for parameters in (
            {"page": 0},
            {"page": 2147483648},
            {"page_size": 201},
            {"search": "x" * 161},
        ):
            assert (await client.get(PATH, params=parameters)).status_code == 422
        assert (await client.post(PATH, json={})).status_code == 405
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": None}
        assert (await client.get(PATH)).status_code == 403
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM public.mixed_customer_return_queue(:tenant,NULL,:search)"),
            {"tenant": original.tenant_id, "search": "x" * 161},
        )
        == 0
    )


async def test_one_stock_kind_intake_does_not_invent_other_kind(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await mixed_source(db_session)
    result = await refund(
        db_session, source, (PhysicalReturnSelection(source.physical_id, "unit", 1),)
    )
    async with queue_actor(db_session):
        response = await client.get(PATH)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    row = response.json()["items"][0]
    assert row["inventory_kind"] == "physical_unit" and row["base_units"] == 1
    assert row["operation_id"] == str(result.operation_id)
    assert row["legacy_quantity"] is None and row["batch_id"] is None
