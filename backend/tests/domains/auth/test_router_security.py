"""Security-boundary checks for auth route dependency wiring."""

from fastapi.routing import APIRoute

from app.domains.auth.router import (
    _auth_state_service,
    _support_auth_state_service,
    router,
)


def test_mfa_step_up_uses_support_auth_state_boundary() -> None:
    route = next(
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path == "/api/v1/auth/mfa/step-up"
    )
    direct_dependencies = {dependency.call for dependency in route.dependant.dependencies}

    assert _support_auth_state_service in direct_dependencies
    assert _auth_state_service not in direct_dependencies


def test_session_management_uses_identity_bound_app_auth_boundary() -> None:
    paths = {
        "/api/v1/auth/sessions",
        "/api/v1/auth/sessions/revoke-others",
        "/api/v1/auth/sessions/{session_id}",
    }
    routes = [
        route for route in router.routes if isinstance(route, APIRoute) and route.path in paths
    ]

    assert {route.path for route in routes} == paths
    for route in routes:
        direct_dependencies = {dependency.call for dependency in route.dependant.dependencies}
        assert _auth_state_service in direct_dependencies
        assert _support_auth_state_service not in direct_dependencies
