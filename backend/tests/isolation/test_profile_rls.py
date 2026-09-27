"""Profile labels stay scoped even when the repository receives forged selections."""

from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.domains.auth.profile_repository import ProfileRepository
from tests.isolation.test_indirect_scope_rls import (
    IndirectScopeRows,
)
from tests.isolation.test_indirect_scope_rls import (
    app_engine_iso as app_engine_iso,  # noqa: PLC0414 -- shared disposable fixtures
)
from tests.isolation.test_indirect_scope_rls import (
    indirect_scope_rows as indirect_scope_rows,  # noqa: PLC0414
)
from tests.isolation.test_indirect_scope_rls import (
    support_engine_iso as support_engine_iso,  # noqa: PLC0414
)


@pytest.mark.usefixtures("app_engine_iso", "indirect_scope_rows")
async def test_profile_repository_preserves_tenant_and_subject_boundaries(
    request: pytest.FixtureRequest,
) -> None:
    engine = cast(AsyncEngine, request.getfixturevalue("app_engine_iso"))
    rows = cast(IndirectScopeRows, request.getfixturevalue("indirect_scope_rows"))
    for user_index, tenant_index, role_id in (
        (0, 0, rows.owner_role_ids[0]),
        (1, 1, rows.owner_role_ids[1]),
        (2, 0, rows.role_ids[2]),
    ):
        tenant_id = UUID(rows.tenant_ids[tenant_index])
        user_id = UUID(rows.user_ids[user_index])
        async with engine.begin() as connection:
            assert (await connection.scalar(text("SELECT current_user"))) == "aurum_app"
            await connection.execute(
                text(
                    "SELECT set_config('app.tenant_id', :tenant, true), "
                    "set_config('app.user_id', :actor, true)"
                ),
                {"tenant": str(tenant_id), "actor": str(user_id)},
            )
            async with AsyncSession(bind=connection) as session:
                repo = ProfileRepository(session)
                selected = [(None, UUID(role)) for role in (*rows.owner_role_ids, *rows.role_ids)]
                own = await repo.assignment_names(
                    user_id=user_id, tenant_id=tenant_id, assignments=selected
                )
                assert [assignment.role_id for assignment in own] == [UUID(role_id)]
                assert all(assignment.branch_id is None for assignment in own)
                foreign = await repo.assignment_names(
                    user_id=UUID(rows.user_ids[1 - tenant_index]),
                    tenant_id=UUID(rows.tenant_ids[1 - tenant_index]),
                    assignments=selected,
                )
                assert foreign == []
