"""RLS isolates electronic refund attempts and references by tenant."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


@pytest_asyncio.fixture
async def support_engine_refund_attempts() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_engine_refund_attempts() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_pos_refund_attempt_rls_enforces_tenant_isolation(
    support_engine_refund_attempts: AsyncEngine,
    app_engine_refund_attempts: AsyncEngine,
) -> None:
    suffix = uuid4().hex[:8]
    tenant_ids: list[str] = []
    user_ids: list[str] = []
    try:
        async with support_engine_refund_attempts.begin() as conn:
            tenants = await conn.execute(
                text(
                    "INSERT INTO tenant (name, contact_email, status) VALUES "
                    "(:name_a, :email_a, 'active'), (:name_b, :email_b, 'active') "
                    "RETURNING id"
                ),
                {
                    "name_a": f"Refund RLS A {suffix}",
                    "email_a": f"refund-rls-a-{suffix}@aurum.tj",
                    "name_b": f"Refund RLS B {suffix}",
                    "email_b": f"refund-rls-b-{suffix}@aurum.tj",
                },
            )
            tenant_ids = [str(row[0]) for row in tenants]
            users = await conn.execute(
                text(
                    "INSERT INTO app_user (email, full_name, home_tenant_id, status) VALUES "
                    "(:email_a, 'A', :tenant_a, 'active'), "
                    "(:email_b, 'B', :tenant_b, 'active') RETURNING id"
                ),
                {
                    "email_a": f"refund-user-a-{suffix}@aurum.tj",
                    "email_b": f"refund-user-b-{suffix}@aurum.tj",
                    "tenant_a": tenant_ids[0],
                    "tenant_b": tenant_ids[1],
                },
            )
            user_ids = [str(row[0]) for row in users]
            branches = await conn.execute(
                text(
                    "INSERT INTO branch (tenant_id, name) VALUES "
                    "(:tenant_a, 'A'), (:tenant_b, 'B') RETURNING id"
                ),
                {"tenant_a": tenant_ids[0], "tenant_b": tenant_ids[1]},
            )
            branch_ids = [str(row[0]) for row in branches]
            registers = await conn.execute(
                text(
                    "INSERT INTO register (tenant_id, branch_id, name) VALUES "
                    "(:tenant_a, :branch_a, 'A'), (:tenant_b, :branch_b, 'B') RETURNING id"
                ),
                {
                    "tenant_a": tenant_ids[0],
                    "tenant_b": tenant_ids[1],
                    "branch_a": branch_ids[0],
                    "branch_b": branch_ids[1],
                },
            )
            register_ids = [str(row[0]) for row in registers]
            shifts = await conn.execute(
                text(
                    "INSERT INTO shift "
                    "(tenant_id, branch_id, register_id, opened_by_user_id, status) VALUES "
                    "(:tenant_a, :branch_a, :register_a, :user_a, 'open'), "
                    "(:tenant_b, :branch_b, :register_b, :user_b, 'open') RETURNING id"
                ),
                {
                    "tenant_a": tenant_ids[0],
                    "tenant_b": tenant_ids[1],
                    "branch_a": branch_ids[0],
                    "branch_b": branch_ids[1],
                    "register_a": register_ids[0],
                    "register_b": register_ids[1],
                    "user_a": user_ids[0],
                    "user_b": user_ids[1],
                },
            )
            shift_ids = [str(row[0]) for row in shifts]
            sales = await conn.execute(
                text(
                    "INSERT INTO sale "
                    "(tenant_id, branch_id, register_id, shift_id, cashier_user_id) VALUES "
                    "(:tenant_a, :branch_a, :register_a, :shift_a, :user_a), "
                    "(:tenant_b, :branch_b, :register_b, :shift_b, :user_b) RETURNING id"
                ),
                {
                    "tenant_a": tenant_ids[0],
                    "tenant_b": tenant_ids[1],
                    "branch_a": branch_ids[0],
                    "branch_b": branch_ids[1],
                    "register_a": register_ids[0],
                    "register_b": register_ids[1],
                    "shift_a": shift_ids[0],
                    "shift_b": shift_ids[1],
                    "user_a": user_ids[0],
                    "user_b": user_ids[1],
                },
            )
            sale_ids = [str(row[0]) for row in sales]
            attempts = await conn.execute(
                text(
                    "INSERT INTO pos_refund_attempt "
                    "(tenant_id, parent_sale_id, register_id, requested_by_user_id, "
                    "operation_id, operation_hash, items_json, external_allocations_json, "
                    "total_amount, external_amount) VALUES "
                    "(:tenant_a, :sale_a, :register_a, :user_a, gen_random_uuid(), "
                    "repeat('a', 64), jsonb_build_array(jsonb_build_object("
                    "'sale_item_id', CAST(:item_a AS text), 'qty', '1')), jsonb_build_array("
                    "jsonb_build_object('payment_method', 'card', 'amount', '10.00')), 10, 10), "
                    "(:tenant_b, :sale_b, :register_b, :user_b, gen_random_uuid(), "
                    "repeat('b', 64), jsonb_build_array(jsonb_build_object("
                    "'sale_item_id', CAST(:item_b AS text), 'qty', '1')), jsonb_build_array("
                    "jsonb_build_object('payment_method', 'qr', 'amount', '20.00')), 20, 20) "
                    "RETURNING id"
                ),
                {
                    "tenant_a": tenant_ids[0],
                    "tenant_b": tenant_ids[1],
                    "sale_a": sale_ids[0],
                    "sale_b": sale_ids[1],
                    "register_a": register_ids[0],
                    "register_b": register_ids[1],
                    "user_a": user_ids[0],
                    "user_b": user_ids[1],
                    "item_a": "00000000-0000-0000-0000-000000000001",
                    "item_b": "00000000-0000-0000-0000-000000000002",
                },
            )
            attempt_ids = [str(row[0]) for row in attempts]
            references = await conn.execute(
                text(
                    "INSERT INTO pos_refund_reference "
                    "(tenant_id, refund_attempt_id, payment_method, amount, terminal_id, "
                    "document_number, confirmed_by_user_id) VALUES "
                    "(:tenant_a, :attempt_a, 'card', 10, 'TERM-A', :doc_a, :user_a), "
                    "(:tenant_b, :attempt_b, 'qr', 20, 'TERM-B', :doc_b, :user_b) "
                    "RETURNING id"
                ),
                {
                    "tenant_a": tenant_ids[0],
                    "tenant_b": tenant_ids[1],
                    "attempt_a": attempt_ids[0],
                    "attempt_b": attempt_ids[1],
                    "doc_a": f"DOC-A-{suffix}",
                    "doc_b": f"DOC-B-{suffix}",
                    "user_a": user_ids[0],
                    "user_b": user_ids[1],
                },
            )
            reference_ids = [str(row[0]) for row in references]

        async with app_engine_refund_attempts.connect() as conn:
            for index in range(2):
                await conn.execute(
                    text("SELECT set_config('app.tenant_id', :value, false)"),
                    {"value": tenant_ids[index]},
                )
                await conn.execute(
                    text("SELECT set_config('app.user_id', :value, false)"),
                    {"value": user_ids[index]},
                )
                visible_attempts = (
                    (await conn.execute(text("SELECT id FROM pos_refund_attempt"))).scalars().all()
                )
                visible_references = (
                    (await conn.execute(text("SELECT id FROM pos_refund_reference")))
                    .scalars()
                    .all()
                )
                assert [str(value) for value in visible_attempts] == [attempt_ids[index]]
                assert [str(value) for value in visible_references] == [reference_ids[index]]

                if index == 0:
                    intent = (
                        await conn.execute(
                            text(
                                "SELECT intent_version, reason_code, comment "
                                "FROM pos_refund_attempt WHERE id = :attempt_id"
                            ),
                            {"attempt_id": attempt_ids[index]},
                        )
                    ).one()
                    assert tuple(intent) == (2, None, None)

                    immutable_updates = (
                        "UPDATE pos_refund_attempt SET intent_version = 1 WHERE id = :attempt_id",
                        "UPDATE pos_refund_attempt SET reason_code = 'other' "
                        "WHERE id = :attempt_id",
                        "UPDATE pos_refund_attempt SET comment = 'tampered' "
                        "WHERE id = :attempt_id",
                    )
                    for statement in immutable_updates:
                        with pytest.raises(
                            DBAPIError,
                            match="Refund attempt identity is immutable",
                        ):
                            async with conn.begin_nested():
                                await conn.execute(
                                    text(statement),
                                    {"attempt_id": attempt_ids[index]},
                                )

                    with pytest.raises(DBAPIError) as invalid_v1:
                        async with conn.begin_nested():
                            await conn.execute(
                                text(
                                    "INSERT INTO pos_refund_attempt "
                                    "(tenant_id, parent_sale_id, register_id, "
                                    "requested_by_user_id, operation_id, operation_hash, "
                                    "items_json, external_allocations_json, intent_version, "
                                    "reason_code, total_amount, external_amount) VALUES "
                                    "(:tenant_id, :sale_id, :register_id, :user_id, "
                                    "gen_random_uuid(), repeat('c', 64), "
                                    "jsonb_build_array(jsonb_build_object("
                                    "'sale_item_id', CAST(:item_id AS text), 'qty', '1')), "
                                    "jsonb_build_array(jsonb_build_object("
                                    "'payment_method', 'card', 'amount', '10.00')), "
                                    "1, 'other', 10, 10)"
                                ),
                                {
                                    "tenant_id": tenant_ids[index],
                                    "sale_id": sale_ids[index],
                                    "register_id": register_ids[index],
                                    "user_id": user_ids[index],
                                    "item_id": "00000000-0000-0000-0000-000000000003",
                                },
                            )
                    assert "ck_pos_refund_attempt_intent_payload" in str(invalid_v1.value)
    finally:
        if tenant_ids:
            async with support_engine_refund_attempts.begin() as conn:
                await conn.execute(
                    text("DELETE FROM tenant WHERE id = ANY(:tenant_ids)"),
                    {"tenant_ids": tenant_ids},
                )
                if user_ids:
                    await conn.execute(
                        text("DELETE FROM app_user WHERE id = ANY(:user_ids)"),
                        {"user_ids": user_ids},
                    )
