"""Decode the JWT (if present) and stash claims on `request.state`.

The actual DB GUC injection (`app.tenant_id`, `app.user_id`, `app.support_session`)
happens in `app.core.deps.get_db`, after the session for this request is opened.
That keeps RLS context bound to the SQLAlchemy transaction, not the connection pool.

Routes that *require* authentication declare a dependency (added in the auth
domain) that raises if `request.state.user_id` is missing.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.errors import AurumError
from app.core.security import decode_token


class AuthContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.removeprefix("Bearer ").strip()
            try:
                claims = decode_token(token)
            except AurumError:
                # Silent: let auth-required dependencies decide. Public routes
                # (e.g. /healthz, /docs, /login) keep working.
                claims = None
            if claims is not None:
                request.state.user_id = claims.get("sub")
                request.state.tenant_id = claims.get("tenant_id")
                request.state.is_support_session = bool(claims.get("support", False))
                # Developers and administrators bypass RLS via the support pool.
                request.state.use_support_pool = bool(
                    claims.get("is_developer", False) or claims.get("is_administrator", False)
                )

        return await call_next(request)
