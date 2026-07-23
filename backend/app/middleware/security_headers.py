"""Response security headers enabled outside development so dev/test (and the
e2e suite) are unaffected.

The backend serves API responses (JSON / XLSX / PDF) and /docs, not the SPA's
HTML (that's served by the static host / reverse proxy). So:
- The hard headers (nosniff, frame DENY, referrer) harden API/doc responses and
  cannot break the SPA.
- CSP is sent as **Report-Only** by default; it never blocks and only reports
  because the effective SPA Content-Security-Policy must live on the frontend
  host that serves index.html. Flip `csp_report_only=False` once a real policy
  has been validated there.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_CSP = "default-src 'self'; base-uri 'none'; object-src 'none'; " "frame-ancestors 'none'"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, enabled: bool, csp_report_only: bool = True) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.enabled = enabled
        self.csp_report_only = csp_report_only

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        if not self.enabled:
            return response
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        csp_header = (
            "Content-Security-Policy-Report-Only"
            if self.csp_report_only
            else "Content-Security-Policy"
        )
        response.headers.setdefault(csp_header, _CSP)
        return response
