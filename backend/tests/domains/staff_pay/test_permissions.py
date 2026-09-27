"""Private pay does not inherit platform or support bypasses."""

from uuid import uuid4

import pytest
from fastapi import Response

from app.core.deps import CurrentUser
from app.core.errors import PermissionDeniedError
from app.domains.staff_pay.router import _member


@pytest.mark.parametrize("kind", ["developer", "administrator", "support"])
async def test_platform_or_support_cannot_enter_private_staff_pay(kind: str) -> None:
    user = CurrentUser(
        user_id=uuid4(),
        tenant_id=uuid4(),
        is_developer=kind == "developer",
        is_administrator=kind == "administrator",
        membership_status="active",
        is_tenant_owner=True,
        permissions={"staff_pay.view", "staff_pay.manage"},
        permission_scopes={"staff_pay.view": None, "staff_pay.manage": None},
        support_access_session_id=uuid4() if kind == "support" else None,
    )
    with pytest.raises(PermissionDeniedError):
        await _member(Response(), user)
