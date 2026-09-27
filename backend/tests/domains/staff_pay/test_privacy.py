"""Staff-pay route privacy without database or authenticated-user fixtures."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI, HTTPException, Response
from sqlalchemy.exc import DBAPIError

import app.domains.staff_pay.router as staff_router_module
from app.core.errors import AuthorizationPolicyDeniedError
from app.domains.staff_pay.router import _PrivateStaffPayRoute
from app.domains.staff_pay.schemas import PeriodUpdate
from app.middleware.error_handler import register_error_handlers

PRIVATE_VALUE = "PRIVATE_NOTE_SENTINEL"


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    register_error_handlers(application)
    private = APIRouter(prefix="/private", route_class=_PrivateStaffPayRoute)

    @private.post("/validate")
    async def validate(payload: PeriodUpdate) -> dict[str, bool]:
        return {"ok": True}

    @private.get("/success")
    async def success() -> Response:
        return Response(content="ok", headers={"X-Result": "preserved", "Cache-Control": "public"})

    @private.get("/http/{status_code}")
    async def http_error(status_code: int) -> None:
        raise HTTPException(
            status_code=status_code,
            detail="Static auth error",
            headers={"WWW-Authenticate": "Bearer", "Retry-After": "60"},
        )

    @private.get("/domain")
    async def domain_error() -> None:
        raise AuthorizationPolicyDeniedError(
            "Missing permission", details={"reason": "permission_required"}
        )

    @private.get("/database")
    async def database_error() -> None:
        raise DBAPIError(
            "SELECT private_payment",
            {"amount": "123456.78", "note": PRIVATE_VALUE},
            Exception(f"PostgreSQL DETAIL contains {PRIVATE_VALUE}"),
            hide_parameters=True,
        )

    @private.get("/output", response_model=int)
    async def invalid_output() -> dict[str, str]:
        return {"amount": "123456.78", "note": PRIVATE_VALUE}

    @application.post("/ordinary")
    async def ordinary(payload: PeriodUpdate) -> dict[str, bool]:
        return {"ok": True}

    application.include_router(private)
    return application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as test_client:
        yield test_client


def _assert_no_store(response: httpx.Response) -> None:
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"


def test_wrapper_is_installed_only_on_staff_pay_routes() -> None:
    assert staff_router_module.router.routes
    assert all(
        isinstance(route, _PrivateStaffPayRoute) for route in staff_router_module.router.routes
    )


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        (
            '{"amount":"123456.789","note":"PRIVATE_NOTE_SENTINEL",' '"expected_row_version":0}',
            ["amount", "expected_row_version"],
        ),
        (
            '{"amount":"1.00","note":"ok","expected_row_version":1,'
            '"PRIVATE_NOTE_SENTINEL":"private"}',
            [],
        ),
        ('{"amount":"PRIVATE_NOTE_SENTINEL",', []),
    ],
)
async def test_validation_never_echoes_input(
    client: httpx.AsyncClient, body: str, fields: list[str]
) -> None:
    response = await client.post(
        "/private/validate", content=body, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "Проверьте данные выплаты",
            "details": {"fields": fields},
        }
    }
    assert PRIVATE_VALUE not in response.text
    assert "123456.789" not in response.text
    _assert_no_store(response)


async def test_success_preserves_headers_and_prevents_caching(client: httpx.AsyncClient) -> None:
    response = await client.get("/private/success")

    assert response.status_code == 200
    assert response.headers["X-Result"] == "preserved"
    _assert_no_store(response)


@pytest.mark.parametrize("status_code", [401, 429])
async def test_http_errors_preserve_auth_and_retry_headers(
    client: httpx.AsyncClient, status_code: int
) -> None:
    response = await client.get(f"/private/http/{status_code}")

    assert response.status_code == status_code
    assert response.json() == {"detail": "Static auth error"}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.headers["Retry-After"] == "60"
    _assert_no_store(response)


async def test_domain_denial_keeps_audit_and_safe_failure_logging(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = AsyncMock(side_effect=RuntimeError(PRIVATE_VALUE))
    logger = Mock()
    monkeypatch.setattr(staff_router_module, "record_authorization_denial", audit)
    monkeypatch.setattr(staff_router_module, "logger", logger)

    response = await client.get("/private/domain")

    assert response.status_code == 403
    assert response.json()["error"]["details"] == {"reason": "permission_required"}
    audit.assert_awaited_once()
    logger.warning.assert_called_once_with("staff_pay_domain_error", code="permission_denied")
    logger.error.assert_called_once_with("staff_pay_denial_audit_failed", error_type="RuntimeError")
    logger.exception.assert_not_called()
    _assert_no_store(response)


@pytest.mark.parametrize(
    ("path", "error_type"), [("database", "DBAPIError"), ("output", "ResponseValidationError")]
)
async def test_unexpected_errors_hide_database_and_output_values(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, path: str, error_type: str
) -> None:
    logger = Mock()
    monkeypatch.setattr(staff_router_module, "logger", logger)

    response = await client.get(f"/private/{path}")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error", "details": {}}
    }
    assert PRIVATE_VALUE not in response.text
    assert "123456.78" not in response.text
    logger.error.assert_called_once_with("staff_pay_request_failed", error_type=error_type)
    logger.exception.assert_not_called()
    _assert_no_store(response)


async def test_ordinary_routes_keep_existing_validation_behavior(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/ordinary",
        json={"amount": PRIVATE_VALUE, "note": "", "expected_row_version": 1},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["input"] == PRIVATE_VALUE
    assert "Cache-Control" not in response.headers
