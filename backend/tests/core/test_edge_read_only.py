"""The Edge shadow HTTP profile cannot mutate local business state."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.middleware.edge_read_only import EdgeReadOnlyMiddleware


def _app(*, enabled: bool) -> Starlette:
    async def endpoint(_request):  # type: ignore[no-untyped-def]
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/resource", endpoint, methods=["GET", "POST"])])
    app.add_middleware(EdgeReadOnlyMiddleware, enabled=enabled)
    return app


async def test_edge_profile_allows_reads_and_blocks_writes() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app(enabled=True)),
        base_url="http://test",
    ) as client:
        read = await client.get("/resource")
        write = await client.post("/resource")

    assert read.status_code == 200
    assert write.status_code == 405
    assert write.json()["error"]["code"] == "edge_read_only"


async def test_cloud_profile_keeps_writes_available() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app(enabled=False)),
        base_url="http://test",
    ) as client:
        response = await client.post("/resource")
    assert response.status_code == 200
