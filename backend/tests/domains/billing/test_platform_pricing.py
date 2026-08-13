"""Protected global billing pricing lifecycle."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.domains.auth.models import AppUser
from tests.auth_helpers import create_support_access_token
from tests.platform_access_helpers import create_test_platform_user


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _platform_identity(
    db_session: AsyncSession,
    *,
    access_kind: str = "developer",
) -> tuple[AppUser, str]:
    user = await create_test_platform_user(db_session, access_kind=access_kind)
    return user, await create_support_access_token(db_session, user)


async def _create_plan_and_draft(
    client: AsyncClient,
    *,
    token: str,
) -> tuple[dict[str, object], dict[str, object]]:
    suffix = uuid4().hex[:12]
    plan_response = await client.post(
        "/api/v1/admin/billing/plans",
        headers=_headers(token),
        json={
            "operation_id": str(uuid4()),
            "code": f"pilot_{suffix}",
            "name": f"Pilot {suffix}",
            "description": "Тестовый тариф для проверки полного жизненного цикла.",
        },
    )
    assert plan_response.status_code == 201, plan_response.text
    plan = plan_response.json()["item"]
    price_response = await client.post(
        f"/api/v1/admin/billing/plans/{plan['plan_id']}/prices",
        headers=_headers(token),
        json={
            "operation_id": str(uuid4()),
            "monthly_price_per_branch": "590.00",
            "annual_discount_pct": "20.00",
            "audience": "default",
            "notice_days": 30,
            "change_reason": "Первоначальная публикация тарифа для пилотного запуска.",
            "terms_snapshot": {"branches_included": 1, "support": "standard"},
        },
    )
    assert price_response.status_code == 201, price_response.text
    return plan, price_response.json()["item"]


async def test_pricing_routes_require_authentication(
    platform_client: AsyncClient,
) -> None:
    responses = (
        await platform_client.get("/api/v1/admin/billing/plans"),
        await platform_client.post(
            "/api/v1/admin/billing/plans",
            json={
                "operation_id": str(uuid4()),
                "code": "unauthorized_plan",
                "name": "Unauthorized plan",
            },
        ),
    )
    assert [response.status_code for response in responses] == [401, 401]


async def test_pricing_plan_and_draft_are_idempotent_and_safe_to_list(
    db_session: AsyncSession,
    platform_client: AsyncClient,
) -> None:
    _, token = await _platform_identity(db_session)
    operation_id = str(uuid4())
    suffix = uuid4().hex[:12]
    payload = {
        "operation_id": operation_id,
        "code": f"idempotent_{suffix}",
        "name": f"Idempotent {suffix}",
        "description": "Повторяемое создание тарифа без второго эффекта.",
    }

    first = await platform_client.post(
        "/api/v1/admin/billing/plans", headers=_headers(token), json=payload
    )
    replay = await platform_client.post(
        "/api/v1/admin/billing/plans", headers=_headers(token), json=payload
    )
    conflict = await platform_client.post(
        "/api/v1/admin/billing/plans",
        headers=_headers(token),
        json={**payload, "name": "Different request"},
    )
    _, other_token = await _platform_identity(db_session)
    cross_actor_replay = await platform_client.post(
        "/api/v1/admin/billing/plans",
        headers=_headers(other_token),
        json=payload,
    )
    listing = await platform_client.get(
        "/api/v1/admin/billing/plans",
        headers=_headers(token),
        params={"page": 1, "page_size": 100},
    )
    empty_page = await platform_client.get(
        "/api/v1/admin/billing/plans",
        headers=_headers(token),
        params={"page": 1000, "page_size": 100},
    )

    assert first.status_code == 201, first.text
    assert first.headers["cache-control"] == "private, no-store"
    assert first.json()["applied"] is True
    assert replay.status_code == 201, replay.text
    assert replay.json()["applied"] is False
    assert replay.json()["item"] == first.json()["item"]
    assert conflict.status_code == 409, conflict.text
    assert cross_actor_replay.status_code == 409, cross_actor_replay.text
    assert listing.status_code == 200, listing.text
    created_plan_id = first.json()["item"]["plan_id"]
    listed = next(item for item in listing.json()["items"] if item["plan_id"] == created_plan_id)
    assert listed["versions"] == []
    assert "terms_snapshot" not in listing.text
    assert empty_page.status_code == 200, empty_page.text
    assert empty_page.json()["items"] == []
    assert empty_page.json()["total"] == listing.json()["total"]


async def test_price_requires_independent_approver_and_can_be_cancelled(
    db_session: AsyncSession,
    platform_client: AsyncClient,
) -> None:
    _, author_token = await _platform_identity(db_session)
    plan, draft = await _create_plan_and_draft(platform_client, token=author_token)
    schedule_payload = {
        "operation_id": str(uuid4()),
        "expected_row_version": draft["row_version"],
        "effective_from": (utc_now() + timedelta(days=31)).isoformat(),
    }
    self_approval = await platform_client.post(
        f"/api/v1/admin/billing/prices/{draft['price_version_id']}/schedule",
        headers=_headers(author_token),
        json=schedule_payload,
    )
    assert self_approval.status_code == 422, self_approval.text

    _, approver_token = await _platform_identity(db_session)
    scheduled = await platform_client.post(
        f"/api/v1/admin/billing/prices/{draft['price_version_id']}/schedule",
        headers=_headers(approver_token),
        json={**schedule_payload, "operation_id": str(uuid4())},
    )
    assert scheduled.status_code == 200, scheduled.text
    assert scheduled.json()["item"]["status"] == "scheduled"
    cancelled = await platform_client.post(
        f"/api/v1/admin/billing/prices/{draft['price_version_id']}/cancel",
        headers=_headers(approver_token),
        json={
            "operation_id": str(uuid4()),
            "expected_row_version": scheduled.json()["item"]["row_version"],
            "reason_code": "commercial_change",
            "reason": "Коммерческие условия будут пересмотрены до публикации.",
        },
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["item"]["status"] == "cancelled"
    assert cancelled.json()["item"]["plan_id"] == plan["plan_id"]


async def test_price_cannot_activate_before_effective_date(
    db_session: AsyncSession,
    platform_client: AsyncClient,
) -> None:
    _, author_token = await _platform_identity(db_session)
    _, draft = await _create_plan_and_draft(platform_client, token=author_token)
    _, approver_token = await _platform_identity(db_session)
    scheduled = await platform_client.post(
        f"/api/v1/admin/billing/prices/{draft['price_version_id']}/schedule",
        headers=_headers(approver_token),
        json={
            "operation_id": str(uuid4()),
            "expected_row_version": draft["row_version"],
            "effective_from": (utc_now() + timedelta(days=31)).isoformat(),
        },
    )
    assert scheduled.status_code == 200, scheduled.text
    activation = await platform_client.post(
        f"/api/v1/admin/billing/prices/{draft['price_version_id']}/activate",
        headers=_headers(approver_token),
        json={
            "operation_id": str(uuid4()),
            "expected_row_version": scheduled.json()["item"]["row_version"],
        },
    )
    assert activation.status_code == 422, activation.text


async def test_price_schedule_rejects_timestamp_without_timezone(
    db_session: AsyncSession,
    platform_client: AsyncClient,
) -> None:
    _, author_token = await _platform_identity(db_session)
    _, draft = await _create_plan_and_draft(platform_client, token=author_token)
    _, approver_token = await _platform_identity(db_session)

    response = await platform_client.post(
        f"/api/v1/admin/billing/prices/{draft['price_version_id']}/schedule",
        headers=_headers(approver_token),
        json={
            "operation_id": str(uuid4()),
            "expected_row_version": draft["row_version"],
            "effective_from": "2030-01-01T10:00:00",
        },
    )

    assert response.status_code == 422, response.text


async def test_price_schedule_rejects_notice_period_violation(
    db_session: AsyncSession,
    platform_client: AsyncClient,
) -> None:
    _, author_token = await _platform_identity(db_session)
    _, draft = await _create_plan_and_draft(platform_client, token=author_token)
    _, approver_token = await _platform_identity(db_session)

    response = await platform_client.post(
        f"/api/v1/admin/billing/prices/{draft['price_version_id']}/schedule",
        headers=_headers(approver_token),
        json={
            "operation_id": str(uuid4()),
            "expected_row_version": draft["row_version"],
            "effective_from": (utc_now() + timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "business_rule_violation"


async def test_activation_archives_previous_price_atomically(
    db_session: AsyncSession,
    platform_client: AsyncClient,
) -> None:
    _, author_token = await _platform_identity(db_session)
    suffix = uuid4().hex[:12]
    plan_response = await platform_client.post(
        "/api/v1/admin/billing/plans",
        headers=_headers(author_token),
        json={
            "operation_id": str(uuid4()),
            "code": f"replace_{suffix}",
            "name": f"Replace {suffix}",
        },
    )
    assert plan_response.status_code == 201, plan_response.text
    plan_id = plan_response.json()["item"]["plan_id"]
    _, approver_token = await _platform_identity(db_session)

    activated_versions: list[dict[str, object]] = []
    for amount in ("500.00", "590.00"):
        draft_response = await platform_client.post(
            f"/api/v1/admin/billing/plans/{plan_id}/prices",
            headers=_headers(author_token),
            json={
                "operation_id": str(uuid4()),
                "monthly_price_per_branch": amount,
                "annual_discount_pct": "20.00",
                "audience": "new_customers",
                "notice_days": 0,
                "change_reason": "Публикация новой цены для новых клиентов.",
                "terms_snapshot": {"revision": amount},
            },
        )
        assert draft_response.status_code == 201, draft_response.text
        draft = draft_response.json()["item"]
        scheduled_response = await platform_client.post(
            f"/api/v1/admin/billing/prices/{draft['price_version_id']}/schedule",
            headers=_headers(approver_token),
            json={
                "operation_id": str(uuid4()),
                "expected_row_version": draft["row_version"],
                "effective_from": (utc_now() + timedelta(seconds=1)).isoformat(),
            },
        )
        assert scheduled_response.status_code == 200, scheduled_response.text
        scheduled = scheduled_response.json()["item"]
        await asyncio.sleep(1.1)
        activated_response = await platform_client.post(
            f"/api/v1/admin/billing/prices/{draft['price_version_id']}/activate",
            headers=_headers(approver_token),
            json={
                "operation_id": str(uuid4()),
                "expected_row_version": scheduled["row_version"],
            },
        )
        assert activated_response.status_code == 200, activated_response.text
        activated_versions.append(activated_response.json()["item"])

    listing = await platform_client.get(
        "/api/v1/admin/billing/plans",
        headers=_headers(approver_token),
        params={"page": 1, "page_size": 100},
    )
    assert listing.status_code == 200, listing.text
    plan = next(item for item in listing.json()["items"] if item["plan_id"] == plan_id)
    statuses = {item["price_version_id"]: item["status"] for item in plan["versions"]}
    assert statuses[activated_versions[0]["price_version_id"]] == "archived"
    assert statuses[activated_versions[1]["price_version_id"]] == "active"
    assert sum(status == "active" for status in statuses.values()) == 1
