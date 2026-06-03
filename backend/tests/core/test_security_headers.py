"""Security-headers middleware: stamped only when enabled (production), and CSP
defaults to Report-Only."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.middleware.security_headers import SecurityHeadersMiddleware


def _app(*, enabled: bool, csp_report_only: bool = True) -> Starlette:
    async def ok(_request):  # type: ignore[no-untyped-def]
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/x", ok)])
    app.add_middleware(SecurityHeadersMiddleware, enabled=enabled, csp_report_only=csp_report_only)
    return app


async def _get(app: Starlette):  # type: ignore[no-untyped-def]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.get("/x")


async def test_headers_present_when_enabled() -> None:
    r = await _get(_app(enabled=True))
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "Referrer-Policy" in r.headers
    assert "Content-Security-Policy-Report-Only" in r.headers
    assert "Content-Security-Policy" not in r.headers  # report-only by default


async def test_headers_absent_when_disabled() -> None:
    r = await _get(_app(enabled=False))
    assert "X-Content-Type-Options" not in r.headers
    assert "X-Frame-Options" not in r.headers
    assert "Content-Security-Policy-Report-Only" not in r.headers


async def test_csp_can_be_enforced() -> None:
    r = await _get(_app(enabled=True, csp_report_only=False))
    assert "Content-Security-Policy" in r.headers
    assert "Content-Security-Policy-Report-Only" not in r.headers
