"""Critical mutations must keep their recent-MFA route boundary."""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.core.deps import require_recent_account_mfa
from app.domains.incoming.router import router as incoming_router
from app.domains.pos.router import router as pos_router


def _route(router, path: str, method: str) -> APIRoute:
    return next(
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path == path and method in (route.methods or set())
    )


def _direct_dependencies(route: APIRoute) -> set[object]:
    return {dependency.call for dependency in route.dependant.dependencies}


def test_critical_inventory_and_refund_mutations_require_recent_mfa() -> None:
    protected_routes = (
        (incoming_router, "/api/v1/incoming/{document_id}/accept", "POST"),
        (incoming_router, "/api/v1/incoming/{document_id}/reject", "POST"),
        (pos_router, "/api/v1/pos/refund-attempts/{attempt_id}/confirm", "POST"),
    )

    for router, path, method in protected_routes:
        assert require_recent_account_mfa in _direct_dependencies(_route(router, path, method))
