"""Critical-action proof must be recent regardless of the chosen account factor."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from app.core.deps import CurrentUser, require_recent_account_mfa
from app.core.errors import PermissionDeniedError
from app.core.time import utc_now


@pytest.mark.parametrize("factor", ["password", "mfa"])
@pytest.mark.parametrize("age_minutes", [None, 11, -2, 0])
async def test_recent_account_confirmation_accepts_only_live_proof(
    factor: str, age_minutes: int | None
) -> None:
    proof = utc_now() - timedelta(minutes=age_minutes) if age_minutes is not None else None
    user = CurrentUser(
        user_id=uuid4(),
        tenant_id=None,
        is_developer=False,
        is_administrator=False,
        session_id=uuid4(),
        password_verified_at=proof if factor == "password" else None,
        mfa_verified_at=proof if factor == "mfa" else None,
    )
    if age_minutes == 0:
        assert await require_recent_account_mfa(user) is user
    else:
        with pytest.raises(PermissionDeniedError) as caught:
            await require_recent_account_mfa(user)
        assert caught.value.details == {"reason": "password_step_up_required"}
