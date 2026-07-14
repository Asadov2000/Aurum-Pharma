"""Fail closed for every HTTP mutation in the Edge shadow deployment profile."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp


class EdgeReadOnlyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, enabled: bool) -> None:
        super().__init__(app)
        self.enabled = enabled

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if self.enabled and request.method not in {"GET", "HEAD", "OPTIONS"}:
            return JSONResponse(
                status_code=405,
                content={
                    "error": {
                        "code": "edge_read_only",
                        "message": "Edge shadow profile is read-only",
                        "details": {},
                    }
                },
            )
        return await call_next(request)
