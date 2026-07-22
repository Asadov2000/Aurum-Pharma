"""Decode the JWT (if present) and stash claims on `request.state`.

The actual DB GUC injection (`app.tenant_id`, `app.user_id`, `app.support_session`)
happens in `app.core.deps.get_db`, after the session for this request is opened.
That keeps RLS context bound to the SQLAlchemy transaction, not the connection pool.

Routes that *require* authentication declare a dependency (added in the auth
domain) that raises if `request.state.user_id` is missing.
"""

from __future__ import annotations

from uuid import UUID

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.errors import AurumError
from app.core.security import decode_token


def _accepts_support_access(path: str, method: str) -> bool:
    if path == "/api/v1/auth/me":
        return method == "GET"
    if path in {"/api/v1/permissions", "/api/v1/templates"}:
        return method == "GET"
    if path == "/api/v1/roles" or path.startswith("/api/v1/roles/"):
        return method in {"GET", "POST", "PATCH"}
    if path == "/api/v1/users" or path.startswith("/api/v1/users/"):
        return method in {"GET", "POST", "PATCH", "DELETE"}
    if path == "/api/v1/branches" or path.startswith("/api/v1/branches/"):
        return method == "GET"
    return False


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
                raw_auth_session_id = claims.get("sid")
                auth_session_id: UUID | None = None
                if isinstance(raw_auth_session_id, str):
                    try:
                        auth_session_id = UUID(raw_auth_session_id)
                    except ValueError:
                        pass
                request.state.auth_session_id = auth_session_id
                is_developer = bool(claims.get("is_developer", False))
                is_administrator = bool(claims.get("is_administrator", False))
                is_admin_route = request.url.path == "/api/v1/admin" or request.url.path.startswith(
                    "/api/v1/admin/"
                )
                accepts_support_access = _accepts_support_access(
                    request.url.path,
                    request.method,
                )
                support_access_id: UUID | None = None
                invalid_support_access = False
                raw_support_access = request.headers.get("X-Aurum-Support-Session", "").strip()
                if (
                    raw_support_access
                    and accepts_support_access
                    and (is_developer or is_administrator)
                ):
                    try:
                        support_access_id = UUID(raw_support_access)
                    except ValueError:
                        invalid_support_access = True
                    if auth_session_id is None:
                        invalid_support_access = True
                # The BYPASSRLS connection is available only to routes that
                # enforce support identity at the FastAPI boundary or present
                # a server-validated short-lived tenant support session.
                platform_support_context = is_admin_route and (is_developer or is_administrator)
                request.state.use_support_pool = platform_support_context
                request.state.is_support_session = platform_support_context
                request.state.support_access_session_id = support_access_id
                request.state.invalid_support_access = invalid_support_access

        return await call_next(request)
