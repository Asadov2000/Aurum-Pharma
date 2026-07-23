"""Inbound request IDs are bounded before they reach responses and logs."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.middleware.request_id import RequestIdMiddleware


def _app() -> Starlette:
    async def ok(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/", ok)])
    app.add_middleware(RequestIdMiddleware)
    return app


async def test_safe_request_id_is_preserved() -> None:
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/", headers={"X-Request-ID": "desktop.sync_42"})

    assert response.headers["X-Request-ID"] == "desktop.sync_42"


async def test_unsafe_or_oversized_request_id_is_replaced() -> None:
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/", headers={"X-Request-ID": "x" * 65})

    generated = response.headers["X-Request-ID"]
    assert generated != "x" * 65
    assert len(generated) == 36
