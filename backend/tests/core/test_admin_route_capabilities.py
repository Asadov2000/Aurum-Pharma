"""Every privileged HTTP route must declare an explicit platform capability."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.main import app


def _dependency_calls(dependant: Dependant) -> Iterator[object]:
    if dependant.call is not None:
        yield dependant.call
    for child in dependant.dependencies:
        yield from _dependency_calls(child)


def test_every_admin_route_declares_platform_capability() -> None:
    unprotected: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1/admin/"):
            continue
        capability_codes = {
            str(vars(call)["platform_capability_code"])
            for call in _dependency_calls(route.dependant)
            if vars(call).get("platform_capability_code") is not None
        }
        if not capability_codes:
            methods = ",".join(sorted(route.methods or ()))
            unprotected.append(f"{methods} {route.path}")

    assert not unprotected, "Admin routes without platform capability: " + "; ".join(unprotected)
