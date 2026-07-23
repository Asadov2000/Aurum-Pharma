"""Forwarded client data is accepted only from the configured reverse proxy."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


def _app() -> Starlette:
    async def client_info(request: Request) -> JSONResponse:
        assert request.client is not None
        return JSONResponse({"host": request.client.host, "scheme": request.url.scheme})

    app = Starlette(routes=[Route("/", client_info)])
    app.add_middleware(
        ProxyHeadersMiddleware,
        trusted_hosts=["172.30.0.10"],
    )
    return app


async def test_trusted_proxy_can_set_client_and_scheme() -> None:
    transport = ASGITransport(app=_app(), client=("172.30.0.10", 43100))
    async with AsyncClient(transport=transport, base_url="http://internal") as client:
        response = await client.get(
            "/",
            headers={
                "X-Forwarded-For": "198.51.100.24",
                "X-Forwarded-Proto": "https",
            },
        )

    assert response.json() == {"host": "198.51.100.24", "scheme": "https"}


async def test_untrusted_source_cannot_spoof_forwarded_client() -> None:
    transport = ASGITransport(app=_app(), client=("172.30.0.99", 43100))
    async with AsyncClient(transport=transport, base_url="http://internal") as client:
        response = await client.get(
            "/",
            headers={
                "X-Forwarded-For": "198.51.100.24",
                "X-Forwarded-Proto": "https",
            },
        )

    assert response.json() == {"host": "172.30.0.99", "scheme": "http"}
