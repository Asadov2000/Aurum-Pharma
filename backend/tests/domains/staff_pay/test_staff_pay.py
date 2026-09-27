"""Private payment HTTP and SQL boundaries with actual aurum_app credentials."""

import importlib.util
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import Request
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.config import get_settings
from app.core.deps import _seed_request_db_context, get_db
from app.core.security import decode_access_token
from app.domains.auth.models import AppUser
from app.domains.foundation.models import Branch, Tenant
from app.domains.roles.models import TenantMembership, UserAssignment
from app.domains.staff_pay.schemas import EntryCreate, PeriodCreate
from app.main import app
from tests.auth_helpers import create_tenant_access_token
from tests.role_version_helpers import create_published_test_role


@dataclass
class Subjects:
    tenant_id: UUID
    foreign_tenant_id: UUID
    manager_id: UUID
    employee_id: UUID
    other_id: UUID
    tokens: dict[str, str]
    session: AsyncSession
    connection: AsyncConnection
    client: AsyncClient

    def headers(self, actor: str = "manager") -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[actor]}"}


@pytest_asyncio.fixture
async def staff(
    maintenance_engine: AsyncEngine,
    db_engine: AsyncEngine,
    client: AsyncClient,
    request: pytest.FixtureRequest,
) -> AsyncIterator[Subjects]:
    factory = async_sessionmaker(maintenance_engine, expire_on_commit=False)
    users: list[UUID] = []
    memberships: list[UUID] = []
    tokens: dict[str, str] = {}
    async with factory() as setup, setup.begin():
        await setup.execute(text("SELECT set_config('app.support_session','true',true)"))
        first = Tenant(
            name="Staff pay test", contact_email=f"{uuid4()}@example.invalid", status="active"
        )
        second = Tenant(
            name="Other pay test", contact_email=f"{uuid4()}@example.invalid", status="active"
        )
        setup.add_all([first, second])
        await setup.flush()
        tenants = [first.id, second.id]
        role = await create_published_test_role(
            setup,
            tenant_id=first.id,
            name="Pay manager",
            permission_codes=["staff_pay.manage", "audit.view.tenant"],
        )
        viewer_role = await create_published_test_role(
            setup, tenant_id=first.id, name="Pay reader", permission_codes=["staff_pay.view"]
        )
        for name in ("manager", "employee", "other", "branch", "foreign", "viewer"):
            tenant_id = second.id if name == "foreign" else first.id
            user = AppUser(
                email=f"{uuid4()}@example.invalid",
                full_name=name,
                home_tenant_id=tenant_id,
                status="active",
            )
            setup.add(user)
            await setup.flush()
            users.append(user.id)
            membership = TenantMembership(
                tenant_id=tenant_id,
                user_id=user.id,
                full_name=name,
                status=(
                    getattr(request, "param", "active")
                    if name == "employee"
                    and getattr(request, "param", None) in {"suspended", "offboarded"}
                    else "active"
                ),
            )
            setup.add(membership)
            await setup.flush()
            memberships.append(membership.id)
            tokens[name] = await create_tenant_access_token(setup, user)
    async with async_sessionmaker(db_engine, expire_on_commit=False)() as support, support.begin():
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        branch = Branch(tenant_id=first.id, name="Branch scope")
        support.add(branch)
        await support.flush()
        for index in (0, 3, 5):
            support.add(
                UserAssignment(
                    tenant_id=first.id,
                    user_id=users[index],
                    membership_id=memberships[index],
                    role_id=viewer_role.id if index == 5 else role.id,
                    branch_id=branch.id if index == 3 else None,
                )
            )
    engine = create_async_engine(
        get_settings().DATABASE_URL_APP, poolclass=NullPool, hide_parameters=True
    )
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:

                async def override(request: Request) -> AsyncIterator[AsyncSession]:
                    await _seed_request_db_context(request, session)
                    yield session

                app.dependency_overrides[get_db] = override
                try:
                    yield Subjects(
                        first.id,
                        second.id,
                        users[0],
                        users[1],
                        users[2],
                        tokens,
                        session,
                        connection,
                        client,
                    )
                finally:
                    app.dependency_overrides.pop(get_db, None)
                    if transaction.is_active:
                        await transaction.rollback()
    finally:
        await engine.dispose()
        if getattr(request, "param", None) != "committed":
            await _cleanup(maintenance_engine, db_engine, tenants, users)


async def _cleanup(
    maintenance: AsyncEngine, support: AsyncEngine, tenants: list[UUID], users: list[UUID]
) -> None:
    async with support.begin() as cleanup:
        for table in ("sync_stream", "sync_writer_epoch", "sync_node"):
            await cleanup.execute(
                text(f"DELETE FROM {table} WHERE tenant_id=ANY(:tenants)"), {"tenants": tenants}
            )
    async with maintenance.begin() as cleanup:
        assert str(await cleanup.scalar(text("SELECT current_database()"))).endswith("_test")
        await cleanup.execute(text("SELECT set_config('app.support_session','true',true)"))
        params = {"tenants": tenants, "users": users}
        for query in (
            "DELETE FROM user_assignment WHERE tenant_id=ANY(:tenants)",
            "DELETE FROM access_role_version_permission WHERE role_version_id IN "
            "(SELECT id FROM access_role_version WHERE tenant_id=ANY(:tenants))",
            "DELETE FROM access_role_version WHERE tenant_id=ANY(:tenants)",
            "DELETE FROM role_permission WHERE role_id IN "
            "(SELECT id FROM role WHERE tenant_id=ANY(:tenants))",
            "DELETE FROM role WHERE tenant_id=ANY(:tenants)",
            "DELETE FROM branch WHERE tenant_id=ANY(:tenants)",
            "DELETE FROM tenant WHERE id=ANY(:tenants)",
            "DELETE FROM app_user WHERE id=ANY(:users)",
        ):
            await cleanup.execute(text(query), params)


async def _period(staff: Subjects, **changes: object) -> dict[str, object]:
    body = {
        "employee_id": str(staff.employee_id),
        "period_from": "2026-01-01",
        "period_to": "2026-01-31",
        "amount": "1000.00",
        "note": "Agreed amount",
        "operation_id": str(uuid4()),
    }
    body.update(changes)
    response = await staff.client.post("/api/v1/staff-pay", json=body, headers=staff.headers())
    assert response.status_code == 201, response.text
    return response.json()


async def test_payment_advance_reversal_revision_and_replay(staff: Subjects) -> None:
    operation = str(uuid4())
    period = await _period(staff, operation_id=operation)
    identifier = period["id"]
    base = f"/api/v1/staff-pay/{identifier}"
    payload = {
        "kind": "advance",
        "amount": "1200.00",
        "paid_on": date.today().isoformat(),
        "note": "Advance",
        "operation_id": str(uuid4()),
    }
    first = await staff.client.post(base + "/entries", json=payload, headers=staff.headers())
    assert first.status_code == 201, first.text
    replay = await staff.client.post(base + "/entries", json=payload, headers=staff.headers())
    assert replay.json()["id"] == first.json()["id"]
    conflict = await staff.client.post(
        base + "/entries", json={**payload, "amount": "1.00"}, headers=staff.headers()
    )
    assert conflict.status_code == 409
    own = await staff.client.get("/api/v1/staff-pay/mine", headers=staff.headers("employee"))
    item = own.json()["items"][0]
    assert (
        item["paid_amount"] == "1200.00"
        and item["advance_amount"] == "1200.00"
        and item["remaining_amount"] == "-200.00"
    )
    assert own.headers["cache-control"] == "private, no-store"
    changed = await staff.client.patch(
        base,
        json={"amount": "1500.00", "note": "Agreed correction", "expected_row_version": 1},
        headers=staff.headers(),
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["row_version"] == 2
    original = await _period(staff, operation_id=operation)
    assert original["amount"] == "1500.00"
    assert (
        await staff.client.patch(
            base,
            json={"amount": "1.00", "note": "", "expected_row_version": 1},
            headers=staff.headers(),
        )
    ).status_code == 409
    history = await staff.client.get(
        f"/api/v1/staff-pay/mine/{identifier}/revisions", headers=staff.headers("employee")
    )
    assert [r["amount"] for r in history.json()["items"]] == ["1500.00", "1000.00"]
    reverse = await staff.client.post(
        base + f"/entries/{first.json()['id']}/reverse",
        json={"reason": "Wrong cash record", "operation_id": str(uuid4())},
        headers=staff.headers(),
    )
    assert reverse.status_code == 201, reverse.text
    assert reverse.json()["amount"] == "1200.00"
    duplicate = await staff.client.post(
        base + f"/entries/{first.json()['id']}/reverse",
        json={"reason": "Again", "operation_id": str(uuid4())},
        headers=staff.headers(),
    )
    assert duplicate.status_code == 409
    own = await staff.client.get("/api/v1/staff-pay/mine", headers=staff.headers("employee"))
    assert own.json()["items"][0]["paid_amount"] == "0"


async def test_own_tenant_branch_and_direct_sql_boundaries(staff: Subjects) -> None:
    period = await _period(staff)
    for actor in ("other", "foreign", "branch"):
        response = await staff.client.get("/api/v1/staff-pay/mine", headers=staff.headers(actor))
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []
        response = await staff.client.get(
            f"/api/v1/staff-pay/mine/{period['id']}/entries", headers=staff.headers(actor)
        )
        assert response.status_code == 404
        assert (
            await staff.client.get("/api/v1/staff-pay", headers=staff.headers(actor))
        ).status_code == 403
    assert (await staff.client.get("/api/v1/staff-pay/mine")).status_code == 401
    viewer = await staff.client.get("/api/v1/staff-pay", headers=staff.headers("viewer"))
    assert viewer.status_code == 200 and viewer.json()["total"] == 1
    assert (
        await staff.client.patch(
            f"/api/v1/staff-pay/{period['id']}",
            json={"amount": "1.00", "note": "", "expected_row_version": 1},
            headers=staff.headers("viewer"),
        )
    ).status_code == 403
    await staff.client.get("/api/v1/staff-pay/mine", headers=staff.headers("other"))
    assert await staff.session.scalar(text("SELECT count(*) FROM staff_pay_period")) == 0
    with pytest.raises(DBAPIError):
        async with staff.session.begin_nested():
            await staff.session.execute(text("UPDATE staff_pay_period SET amount=0"))
    with pytest.raises(DBAPIError):
        async with staff.session.begin_nested():
            await staff.session.execute(
                text("SELECT command_staff_pay('create',NULL,NULL,:op,'{}'::jsonb)"),
                {"op": uuid4()},
            )


async def test_overlap_future_date_and_private_audit(
    staff: Subjects, maintenance_engine: AsyncEngine
) -> None:
    period = await _period(staff)
    response = await staff.client.post(
        "/api/v1/staff-pay",
        json={
            "employee_id": str(staff.employee_id),
            "period_from": "2026-01-31",
            "period_to": "2026-02-28",
            "amount": "100.00",
            "note": "",
            "operation_id": str(uuid4()),
        },
        headers=staff.headers(),
    )
    assert response.status_code == 409
    response = await staff.client.post(
        f"/api/v1/staff-pay/{period['id']}/entries",
        json={
            "kind": "payment",
            "amount": "1.00",
            "paid_on": (date.today() + timedelta(days=3)).isoformat(),
            "operation_id": str(uuid4()),
        },
        headers=staff.headers(),
    )
    assert response.status_code == 422
    response = await staff.client.post(
        "/api/v1/staff-pay/employees/search", json={"q": "employee"}, headers=staff.headers()
    )
    assert response.status_code == 200, response.text
    assert set(response.json()["items"][0]) == {"id", "full_name", "status"}
    # Cross-pool SQL cannot read this transaction's rows; test ACL itself instead.
    async with maintenance_engine.connect() as conn:
        for table in ("staff_pay_period", "staff_pay_entry", "staff_pay_revision"):
            assert not await conn.scalar(
                text("SELECT has_table_privilege('aurum_support',:table,'SELECT')"),
                {"table": table},
            )
    audit = await staff.session.execute(
        text(
            "SELECT metadata,old_values,new_values FROM audit_log "
            "WHERE table_name='staff_pay_period' AND record_id=:id"
        ),
        {"id": UUID(str(period["id"]))},
    )
    audit_rows = audit.all()
    assert audit_rows
    for row in audit_rows:
        assert "1000" not in str(row) and "Agreed amount" not in str(row)


async def test_revoked_session_cannot_start_payment(
    staff: Subjects, db_engine: AsyncEngine
) -> None:
    claims = decode_access_token(staff.tokens["manager"])
    async with db_engine.begin() as support:
        await support.execute(
            text("UPDATE session SET revoked_at=now() WHERE id=:id"), {"id": UUID(claims["sid"])}
        )
    response = await staff.client.get("/api/v1/staff-pay", headers=staff.headers())
    assert response.status_code == 401
    with pytest.raises(DBAPIError):
        async with staff.session.begin_nested():
            await staff.session.execute(
                text("SELECT command_staff_pay('create',NULL,NULL,:op,'{}'::jsonb)"),
                {"op": uuid4()},
            )


@pytest.mark.parametrize(
    "value", [1, 1.1, "1.001", "NaN", "Infinity", "-1", "1000000000000", " 1.00"]
)
def test_money_is_exact_string(value: object) -> None:
    with pytest.raises(ValueError):
        PeriodCreate.model_validate(
            {
                "employee_id": uuid4(),
                "period_from": "2026-01-01",
                "period_to": "2026-01-31",
                "amount": value,
                "operation_id": uuid4(),
            }
        )


def test_zero_payment_rejected() -> None:
    with pytest.raises(ValueError):
        EntryCreate.model_validate(
            {"kind": "payment", "amount": "0.00", "paid_on": "2026-01-01", "operation_id": uuid4()}
        )


@pytest.mark.parametrize("staff", ["suspended", "offboarded"], indirect=True)
async def test_inactive_employee_can_receive_final_pay_but_cannot_read(staff: Subjects) -> None:
    period = await _period(staff)
    response = await staff.client.get("/api/v1/staff-pay/mine", headers=staff.headers("employee"))
    assert response.status_code == 401
    response = await staff.client.post(
        f"/api/v1/staff-pay/{period['id']}/entries",
        json={
            "kind": "payment",
            "amount": "1000.00",
            "paid_on": date.today().isoformat(),
            "note": "Final payment",
            "operation_id": str(uuid4()),
        },
        headers=staff.headers(),
    )
    assert response.status_code == 201, response.text


async def test_empty_migration_roundtrip(maintenance_engine: AsyncEngine) -> None:
    path = Path(__file__).parents[3] / "alembic" / "versions" / "0160_manual_staff_pay.py"
    spec = importlib.util.spec_from_file_location("staff_pay_migration", path.resolve())
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")

        def roundtrip(sync_connection):
            with Operations.context(MigrationContext.configure(sync_connection)):
                module.downgrade()
                module.upgrade()

        try:
            await connection.run_sync(roundtrip)
            assert await connection.scalar(text("SELECT to_regclass('public.staff_pay_period')"))
        finally:
            await transaction.rollback()


@pytest.mark.parametrize("staff", ["committed"], indirect=True)
async def test_revoked_manage_cannot_replay_and_private_tables_are_immutable(
    staff: Subjects, db_engine: AsyncEngine
) -> None:
    operation = str(uuid4())
    period = await _period(staff, operation_id=operation)
    response = await staff.client.post(
        f"/api/v1/staff-pay/{period['id']}/entries",
        json={
            "kind": "payment",
            "amount": "10.00",
            "paid_on": date.today().isoformat(),
            "operation_id": str(uuid4()),
        },
        headers=staff.headers(),
    )
    assert response.status_code == 201, response.text
    for table in ("staff_pay_entry", "staff_pay_revision"):
        for statement in (f"UPDATE {table} SET amount=0", f"DELETE FROM {table}"):
            with pytest.raises(DBAPIError):
                async with staff.session.begin_nested():
                    await staff.session.execute(text(statement))
    await staff.session.commit()
    await staff.connection.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SET LOCAL statement_timeout='10s'"))
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE user_assignment SET is_active=false WHERE tenant_id=:t AND user_id=:u"),
            {"t": staff.tenant_id, "u": staff.manager_id},
        )
    replay = await staff.client.post(
        "/api/v1/staff-pay",
        json={
            "employee_id": str(staff.employee_id),
            "period_from": "2026-01-01",
            "period_to": "2026-01-31",
            "amount": "1000.00",
            "note": "Agreed amount",
            "operation_id": operation,
        },
        headers=staff.headers(),
    )
    assert replay.status_code == 403, replay.text
    with pytest.raises(DBAPIError):
        async with staff.session.begin_nested():
            await staff.session.execute(
                text("SELECT command_staff_pay('create',NULL,NULL,:op,'{}'::jsonb)"),
                {"op": UUID(operation)},
            )
    async with db_engine.connect() as support:
        for table in ("staff_pay_period", "staff_pay_entry", "staff_pay_revision"):
            with pytest.raises(DBAPIError):
                async with support.begin_nested():
                    await support.execute(text(f"SELECT count(*) FROM {table}"))
