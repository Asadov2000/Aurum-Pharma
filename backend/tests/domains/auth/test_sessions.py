"""Self-service active-session inventory and revocation."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_token
from app.core.time import utc_now
from app.domains.auth.models import AppUser, Session


async def _set_actor(db_session: AsyncSession, user_id: UUID) -> None:
    await db_session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(user_id)},
    )


async def _create_session(
    db_session: AsyncSession,
    *,
    user: AppUser,
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0) Chrome/126.0",
    ip_address: str = "203.0.113.42",
    expires_in: timedelta = timedelta(days=14),
    revoked: bool = False,
) -> Session:
    session = Session(
        user_id=user.id,
        refresh_token_hash=hash_token(str(uuid4())),
        user_agent=user_agent,
        ip_address=ip_address,
        expires_at=utc_now() + expires_in,
        revoked_at=utc_now() if revoked else None,
        revoked_reason="test" if revoked else None,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)
    return session


def _auth_headers(user: AppUser, session: Session) -> dict[str, str]:
    token = create_access_token(
        user.id,
        tenant_id=user.home_tenant_id,
        is_developer=user.is_developer,
        is_administrator=user.is_administrator,
        session_id=session.id,
    )
    return {"Authorization": f"Bearer {token}"}


async def test_session_inventory_returns_only_safe_active_user_sessions(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    user = await make_user(email="session-owner@aurum.tj")
    other_user = await make_user(email="other-session-owner@aurum.tj")
    current = await _create_session(db_session, user=user)
    other = await _create_session(
        db_session,
        user=user,
        user_agent="Mozilla/5.0 (Linux; Android 14) Chrome/126.0",
        ip_address="2001:db8:abcd:1234:1111:2222:3333:4444",
    )
    await _create_session(db_session, user=user, expires_in=timedelta(minutes=-1))
    await _create_session(db_session, user=user, revoked=True)
    await _create_session(db_session, user=other_user)
    await _set_actor(db_session, user.id)

    response = await auth_client.get(
        "/api/v1/auth/sessions",
        headers=_auth_headers(user, current),
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert [item["id"] for item in body["items"]] == [str(current.id), str(other.id)]
    assert body["items"][0]["is_current"] is True
    assert body["items"][0]["ip_address"] == "203.0.x.x"
    assert body["items"][1]["ip_address"] == "2001:db8:abcd:1234::/64"
    assert all("refresh_token_hash" not in item for item in body["items"])
    assert all("mfa_verified_at" not in item for item in body["items"])


async def test_current_session_cannot_be_revoked_from_inventory(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    user = await make_user(email="current-session@aurum.tj")
    current = await _create_session(db_session, user=user)
    await _set_actor(db_session, user.id)

    response = await auth_client.delete(
        f"/api/v1/auth/sessions/{current.id}",
        headers=_auth_headers(user, current),
    )

    assert response.status_code == 409
    await db_session.refresh(current)
    assert current.revoked_at is None


async def test_revoke_session_is_owner_bound_and_invalidates_access_immediately(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    user = await make_user(email="revoke-session@aurum.tj")
    other_user = await make_user(email="unrelated-session@aurum.tj")
    current = await _create_session(db_session, user=user)
    target = await _create_session(db_session, user=user)
    unrelated = await _create_session(db_session, user=other_user)
    await _set_actor(db_session, user.id)

    forbidden = await auth_client.delete(
        f"/api/v1/auth/sessions/{unrelated.id}",
        headers=_auth_headers(user, current),
    )
    assert forbidden.status_code == 404

    revoked = await auth_client.delete(
        f"/api/v1/auth/sessions/{target.id}",
        headers=_auth_headers(user, current),
    )
    assert revoked.status_code == 200
    assert revoked.json() == {"status": "ok", "revoked_count": 1}

    await db_session.refresh(target)
    await db_session.refresh(unrelated)
    assert target.revoked_reason == "user_revoked"
    assert unrelated.revoked_at is None

    # The shared auth fixture uses the support role for isolated setup. Turn
    # its explicit support context off before checking the runtime app path.
    await db_session.execute(text("SELECT set_config('app.support_session', 'false', true)"))
    stale_access = await auth_client.get(
        "/api/v1/auth/me",
        headers=_auth_headers(user, target),
    )
    assert stale_access.status_code == 401


async def test_revoke_other_sessions_keeps_current_and_other_users(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    user = await make_user(email="revoke-others@aurum.tj")
    other_user = await make_user(email="keep-other-user@aurum.tj")
    current = await _create_session(db_session, user=user)
    first_other = await _create_session(db_session, user=user)
    second_other = await _create_session(db_session, user=user)
    unrelated = await _create_session(db_session, user=other_user)
    current_headers = _auth_headers(user, current)
    await _set_actor(db_session, user.id)

    response = await auth_client.post(
        "/api/v1/auth/sessions/revoke-others",
        headers=current_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "revoked_count": 2}

    current_id = current.id
    first_other_id = first_other.id
    second_other_id = second_other.id
    unrelated_id = unrelated.id
    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(Session)
            .where(Session.id.in_([current_id, first_other_id, second_other_id, unrelated_id]))
            .execution_options(populate_existing=True)
        )
    ).scalars()
    by_id = {session.id: session for session in rows}
    assert by_id[current_id].revoked_at is None
    assert by_id[first_other_id].revoked_reason == "user_revoked_others"
    assert by_id[second_other_id].revoked_reason == "user_revoked_others"
    assert by_id[unrelated_id].revoked_at is None

    repeated = await auth_client.post(
        "/api/v1/auth/sessions/revoke-others",
        headers=current_headers,
    )
    assert repeated.status_code == 200
    assert repeated.json()["revoked_count"] == 0
