"""Security-boundary checks for ownership-transfer route wiring."""

from fastapi.routing import APIRoute

from app.core.deps import (
    current_user,
    require_recent_account_mfa,
    require_recent_owner_mfa,
)
from app.domains.roles.router import router


def _route(path: str, method: str) -> APIRoute:
    return next(
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path == path and method in (route.methods or set())
    )


def _direct_dependencies(route: APIRoute) -> set[object]:
    return {dependency.call for dependency in route.dependant.dependencies}


def test_ownership_transfer_routes_keep_role_specific_mfa_boundaries() -> None:
    listing = _direct_dependencies(_route("/api/v1/ownership-transfers", "GET"))
    creation = _direct_dependencies(_route("/api/v1/ownership-transfers", "POST"))
    cancellation = _direct_dependencies(
        _route("/api/v1/ownership-transfers/{request_id}/cancel", "POST")
    )
    acceptance = _direct_dependencies(
        _route("/api/v1/ownership-transfers/{request_id}/accept", "POST")
    )

    assert current_user in listing
    assert require_recent_owner_mfa in creation
    assert require_recent_owner_mfa in cancellation
    assert require_recent_account_mfa in acceptance
    assert require_recent_owner_mfa not in acceptance
