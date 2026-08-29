"""Fail-closed inventory for branch-scoped HTTP routes."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    BRANCH_SCOPED_PERMISSIONS,
    CurrentUser,
    require_any_branch_permission,
    require_branch_permission,
)
from app.core.errors import PermissionDeniedError
from app.main import app

_BRANCH_POLICIES = {"direct", "filter", "resource", "tenant_reference"}


def _dependency_calls(dependant: Dependant) -> Iterator[object]:
    if dependant.call is not None:
        yield dependant.call
    for child in dependant.dependencies:
        yield from _dependency_calls(child)


def test_every_branch_scoped_route_declares_scope_policy() -> None:
    unclassified: list[str] = []
    invalid: list[str] = []

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for call in _dependency_calls(route.dependant):
            metadata = vars(call)
            permission_codes = set(metadata.get("permission_codes", ()))
            branch_codes = permission_codes.intersection(BRANCH_SCOPED_PERMISSIONS)
            if not branch_codes:
                continue
            route_label = f"{','.join(sorted(route.methods or ()))} {route.path}"
            policy = metadata.get("branch_scope_policy")
            if policy is None:
                unclassified.append(f"{route_label}: {','.join(sorted(branch_codes))}")
            elif policy not in _BRANCH_POLICIES:
                invalid.append(f"{route_label}: {policy}")

    assert not unclassified, "Branch routes without scope policy: " + "; ".join(unclassified)
    assert not invalid, "Branch routes with invalid scope policy: " + "; ".join(invalid)


@pytest.mark.asyncio
async def test_branch_permission_registry_matches_database(db_session: AsyncSession) -> None:
    result = await db_session.execute(text("""
            SELECT code
            FROM public.permission
            WHERE is_active = true AND scope_type = 'BRANCH_SET'
            ORDER BY code
            """))
    database_codes = frozenset(str(code) for code in result.scalars())

    assert BRANCH_SCOPED_PERMISSIONS == database_codes


@pytest.mark.asyncio
async def test_branch_dependency_denies_missing_or_empty_scope() -> None:
    branch_id = uuid4()
    base = {
        "user_id": uuid4(),
        "tenant_id": uuid4(),
        "is_developer": False,
        "is_administrator": False,
    }
    checker = require_branch_permission("pos.sell", policy="direct")

    with pytest.raises(PermissionDeniedError):
        await checker(CurrentUser(**base))

    with pytest.raises(PermissionDeniedError):
        await checker(
            CurrentUser(
                **base,
                permissions={"pos.sell"},
                permission_scopes={"pos.sell": frozenset()},
            )
        )

    scoped = CurrentUser(
        **base,
        permissions={"pos.sell"},
        permission_scopes={"pos.sell": frozenset({branch_id})},
    )
    assert await checker(scoped) is scoped

    tenant_wide = CurrentUser(
        **base,
        permissions={"pos.sell"},
        permission_scopes={"pos.sell": None},
    )
    assert await checker(tenant_wide) is tenant_wide


@pytest.mark.asyncio
async def test_any_branch_dependency_uses_only_usable_grants() -> None:
    base = {
        "user_id": uuid4(),
        "tenant_id": uuid4(),
        "is_developer": False,
        "is_administrator": False,
    }
    checker = require_any_branch_permission(
        "catalog.view",
        "pos.sell",
        policy="filter",
    )

    with pytest.raises(PermissionDeniedError):
        await checker(
            CurrentUser(
                **base,
                permissions={"pos.sell"},
                permission_scopes={"pos.sell": frozenset()},
            )
        )

    tenant_catalog = CurrentUser(
        **base,
        permissions={"catalog.view"},
        permission_scopes={"catalog.view": None},
    )
    assert await checker(tenant_catalog) is tenant_catalog
