"""Domain error handling preserves responses and audits policy denials."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

import app.middleware.error_handler as error_handler_module
from app.core.errors import AuthorizationPolicyDeniedError, PermissionDeniedError


def _request() -> Request:
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/api/v1/users/example/assignments",
            "raw_path": b"/api/v1/users/example/assignments",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("aurum.test", 443),
            "state": {},
            "route": SimpleNamespace(path="/api/v1/users/{user_id}/assignments"),
        }
    )
    request.state.user_id = uuid4()
    request.state.tenant_id = uuid4()
    request.state.request_id = "request-123"
    return request


async def test_policy_denial_is_audited_without_changing_the_403(
    monkeypatch,
) -> None:
    audit = AsyncMock()
    monkeypatch.setattr(error_handler_module, "record_authorization_denial", audit)
    request = _request()
    error = AuthorizationPolicyDeniedError(
        "Assignment denied",
        details={"reason": "self_assignment_denied"},
    )

    response = await error_handler_module.aurum_error_handler(request, error)

    assert response.status_code == 403
    assert json.loads(response.body)["error"]["code"] == "permission_denied"
    audit.assert_awaited_once_with(request, error)


async def test_ordinary_permission_denial_does_not_create_policy_audit(
    monkeypatch,
) -> None:
    audit = AsyncMock()
    monkeypatch.setattr(error_handler_module, "record_authorization_denial", audit)
    request = _request()

    response = await error_handler_module.aurum_error_handler(
        request,
        PermissionDeniedError("Missing permission"),
    )

    assert response.status_code == 403
    audit.assert_not_awaited()


async def test_authorization_denial_is_committed_in_a_separate_transaction(
    maintenance_engine: AsyncEngine,
) -> None:
    tenant_id = uuid4()
    user_id = uuid4()
    request = _request()
    request.state.tenant_id = tenant_id
    request.state.user_id = user_id
    error = AuthorizationPolicyDeniedError(
        "Assignment denied",
        details={
            "reason": "self_assignment_denied",
            "permissions": ["roles.assign"],
        },
    )

    try:
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("""
                    INSERT INTO public.tenant (id, name, contact_email, status)
                    VALUES (:tenant_id, :name, :email, 'active')
                    """),
                {
                    "tenant_id": tenant_id,
                    "name": f"Denial audit {tenant_id}",
                    "email": f"denial-{tenant_id}@aurum.test",
                },
            )
            await connection.execute(
                text("""
                    INSERT INTO public.app_user (
                      id, email, full_name, home_tenant_id, status, activated_at
                    ) VALUES (
                      :user_id, :email, 'Denied actor', :tenant_id,
                      'active', pg_catalog.statement_timestamp()
                    )
                    """),
                {
                    "user_id": user_id,
                    "email": f"denied-actor-{user_id}@aurum.test",
                    "tenant_id": tenant_id,
                },
            )

        await error_handler_module.record_authorization_denial(request, error)

        async with maintenance_engine.connect() as connection:
            result = (
                (
                    await connection.execute(
                        text("""
                        SELECT action, table_name, metadata
                        FROM public.audit_log
                        WHERE tenant_id = :tenant_id
                          AND action = 'AUTHORIZATION_DENIED'
                        """),
                        {"tenant_id": tenant_id},
                    )
                )
                .mappings()
                .one()
            )

        assert result["action"] == "AUTHORIZATION_DENIED"
        assert result["table_name"] == "authorization_policy"
        assert result["metadata"] == {
            "result": "denied",
            "reason": "self_assignment_denied",
            "method": "POST",
            "path": "/api/v1/users/{user_id}/assignments",
            "request_id": "request-123",
            "permissions": ["roles.assign"],
        }
    finally:
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM public.audit_log WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await connection.execute(
                text("DELETE FROM public.app_user WHERE id = :user_id"),
                {"user_id": user_id},
            )
            await connection.execute(
                text("DELETE FROM public.tenant WHERE id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
