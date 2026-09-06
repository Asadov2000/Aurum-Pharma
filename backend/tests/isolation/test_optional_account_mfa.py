"""Self-service MFA and password confirmation retain account/session boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

PASSWORD_HASH = "test-only-verified-password-hash-" + "a" * 40


@dataclass(frozen=True)
class Account:
    user_id: UUID
    session_id: UUID
    other_session_id: UUID


async def _context(
    connection: AsyncConnection, account: Account, *, user_id: UUID | None = None
) -> None:
    await connection.execute(
        text(
            "SELECT set_config('app.user_id', :user_id, true), "
            "set_config('app.auth_session_id', :session_id, true), "
            "set_config('app.support_session', 'true', true), "
            "set_config('app.tenant_id', '', true)"
        ),
        {"user_id": str(user_id or account.user_id), "session_id": str(account.session_id)},
    )


@pytest_asyncio.fixture
async def account(db_connection: AsyncConnection) -> Account:
    account = Account(uuid4(), uuid4(), uuid4())
    await db_connection.execute(text("SELECT set_config('app.support_session','true',true)"))
    await db_connection.execute(
        text(
            "INSERT INTO public.app_user(id,email,full_name,status,password_hash) "
            "VALUES(:user_id,:email,'Optional MFA test','active',:password_hash)"
        ),
        {
            "user_id": account.user_id,
            "email": f"optional-{account.user_id}@example.test",
            "password_hash": PASSWORD_HASH,
        },
    )
    for session_id in (account.session_id, account.other_session_id):
        await db_connection.execute(
            text(
                "INSERT INTO public.session(id,user_id,refresh_token_hash,expires_at) "
                "VALUES(:id,:user_id,:hash,now()+INTERVAL '1 hour')"
            ),
            {"id": session_id, "user_id": account.user_id, "hash": uuid4().hex + uuid4().hex},
        )
    await _context(db_connection, account)
    return account


async def test_settings_require_the_current_account_and_session(
    db_connection: AsyncConnection, account: Account
) -> None:
    parameters = {"user_id": account.user_id, "session_id": account.session_id}
    lookup = text("SELECT * FROM public.lookup_account_mfa_settings(:user_id,:session_id)")
    row = (await db_connection.execute(lookup, parameters)).mappings().one()
    assert row["status"] is None
    assert row["prompt_dismissed_at"] is None
    assert row["password_configured"] is True
    assert not await db_connection.scalar(
        text("SELECT public.lookup_auth_account_mfa_requirement(:user_id,:session_id)"), parameters
    )
    assert (
        await db_connection.execute(lookup, {**parameters, "session_id": account.other_session_id})
    ).first() is None
    await _context(db_connection, account, user_id=uuid4())
    assert (await db_connection.execute(lookup, parameters)).first() is None
    assert not await db_connection.scalar(
        text("SELECT public.dismiss_account_mfa_prompt(:user_id,:session_id)"), parameters
    )
    await _context(db_connection, account)
    assert await db_connection.scalar(
        text("SELECT public.dismiss_account_mfa_prompt(:user_id,:session_id)"), parameters
    )
    assert (await db_connection.execute(lookup, parameters)).mappings().one()["prompt_dismissed_at"]


async def test_password_proof_is_atomic_session_bound_and_never_mfa(
    db_connection: AsyncConnection, account: Account
) -> None:
    parameters = {
        "user_id": account.user_id,
        "session_id": account.session_id,
        "hash": "stale-hash",
    }
    confirm = text("SELECT public.confirm_account_password(:user_id,:session_id,:hash)")
    assert await db_connection.scalar(confirm, parameters) is None
    parameters["hash"] = PASSWORD_HASH
    stamp = await db_connection.scalar(confirm, parameters)
    assert isinstance(stamp, datetime)
    assert (
        await db_connection.scalar(
            text("SELECT mfa_verified_at FROM public.session WHERE id=:session_id"), parameters
        )
        is None
    )
    assert await db_connection.scalar(text("SELECT public.current_auth_confirmation_at()")) is None
    for delta in (-1, 1):
        await db_connection.execute(
            text("SELECT set_config('app.password_verified_at',:claim,true)"),
            {"claim": str(int(stamp.timestamp()) + delta)},
        )
        assert (
            await db_connection.scalar(text("SELECT public.current_auth_confirmation_at()")) is None
        )
    await db_connection.execute(
        text("SELECT set_config('app.password_verified_at',:claim,true)"),
        {"claim": str(int(stamp.timestamp()))},
    )
    assert await db_connection.scalar(text("SELECT public.current_auth_confirmation_at()"))
    await db_connection.execute(
        text("UPDATE public.session SET revoked_at=now() WHERE id=:session_id"), parameters
    )
    assert await db_connection.scalar(text("SELECT public.current_auth_confirmation_at()")) is None
    assert await db_connection.scalar(confirm, parameters) is None


async def test_missing_context_cannot_confirm_password(
    db_connection: AsyncConnection, account: Account
) -> None:
    await db_connection.execute(text("SELECT set_config('app.auth_session_id','',true)"))
    assert (
        await db_connection.scalar(
            text("SELECT public.confirm_account_password(:user_id,:session_id,:hash)"),
            {"user_id": account.user_id, "session_id": account.session_id, "hash": PASSWORD_HASH},
        )
        is None
    )


async def test_support_identity_lookup_rejects_revoked_or_foreign_session(
    db_connection: AsyncConnection, account: Account
) -> None:
    lookup = text("SELECT public.lookup_auth_account_mfa_requirement(:user_id,:session_id)")
    parameters = {"user_id": account.user_id, "session_id": account.session_id}
    assert await db_connection.scalar(lookup, parameters) is False
    assert await db_connection.scalar(lookup, {**parameters, "session_id": uuid4()}) is None
    await db_connection.execute(
        text("UPDATE public.session SET revoked_at=now() WHERE id=:session_id"), parameters
    )
    assert await db_connection.scalar(lookup, parameters) is None


async def test_enrollment_is_bound_to_live_origin_and_cannot_replace_enabled_factor(
    db_connection: AsyncConnection, account: Account
) -> None:
    parameters = {
        "user_id": account.user_id,
        "session_id": account.session_id,
        "token_hash": uuid4().hex + uuid4().hex,
        "hash": PASSWORD_HASH,
    }
    begin = text(
        "SELECT * FROM public.create_authenticated_mfa_challenge("
        ":user_id,:session_id,:token_hash,:hash,'127.0.0.1','test',now()+INTERVAL '5 minutes')"
    )
    assert (await db_connection.execute(begin, parameters)).mappings().one()["purpose"] == "enroll"
    lookup = text("SELECT * FROM public.lookup_auth_mfa_challenge(:token_hash,'{}'::JSONB,false)")
    assert (await db_connection.execute(lookup, parameters)).first() is not None
    await db_connection.execute(
        text("UPDATE public.session SET revoked_at=now() WHERE id=:session_id"), parameters
    )
    assert (await db_connection.execute(lookup, parameters)).first() is None
    await db_connection.execute(
        text("UPDATE public.session SET revoked_at=NULL WHERE id=:session_id"), parameters
    )
    await db_connection.execute(
        text(
            "INSERT INTO public.support_mfa(user_id,status,active_secret_ciphertext,"
            "active_key_version,active_generation) "
            "VALUES(:user_id,'active',decode('aabb','hex'),1,1)"
        ),
        parameters,
    )
    assert (
        await db_connection.execute(begin, {**parameters, "token_hash": uuid4().hex + uuid4().hex})
    ).first() is None
    codes = [uuid4().hex + uuid4().hex for _ in range(10)]
    assert not await db_connection.scalar(
        text(
            "SELECT public.stage_auth_mfa_enrollment(:token_hash,:secret,1::SMALLINT,:key,:codes)"
        ),
        {**parameters, "secret": "A" * 32, "key": "b" * 64, "codes": codes},
    )
    assert (
        await db_connection.scalar(
            text(
                "SELECT encode(active_secret_ciphertext,'hex') FROM public.support_mfa "
                "WHERE user_id=:user_id"
            ),
            parameters,
        )
        == "aabb"
    )


async def test_disable_requires_password_and_live_current_session_that_passed_mfa(
    db_connection: AsyncConnection, account: Account
) -> None:
    recovery_hash = uuid4().hex + uuid4().hex
    parameters = {
        "user_id": account.user_id,
        "session_id": account.session_id,
        "hash": PASSWORD_HASH,
        "recovery_hash": recovery_hash,
        "refresh_hash": uuid4().hex + uuid4().hex,
    }
    await db_connection.execute(
        text(
            "INSERT INTO public.support_mfa(user_id,status,active_secret_ciphertext,"
            "active_key_version,active_generation) "
            "VALUES(:user_id,'active',decode('aabb','hex'),1,1)"
        ),
        parameters,
    )
    await db_connection.execute(
        text(
            "INSERT INTO public.support_mfa_recovery_code("
            "user_id,generation,code_hash,activated_at) "
            "VALUES(:user_id,1,:recovery_hash,now())"
        ),
        parameters,
    )
    disable = text(
        "SELECT public.disable_account_mfa(:user_id,:session_id,:hash,"
        ":refresh_hash,'test','127.0.0.1',now()+INTERVAL '1 hour')"
    )
    # A password alone cannot turn an unverified session into authority to remove MFA.
    assert await db_connection.scalar(disable, parameters) is None
    await db_connection.execute(
        text("UPDATE public.session SET mfa_verified_at=now() WHERE id=:session_id"), parameters
    )
    assert await db_connection.scalar(disable, {**parameters, "hash": "stale"}) is None
    assert (
        await db_connection.scalar(disable, {**parameters, "session_id": account.other_session_id})
        is None
    )
    new_session = await db_connection.scalar(disable, parameters)
    assert isinstance(new_session, UUID)
    assert (
        await db_connection.scalar(
            text("SELECT count(*) FROM public.support_mfa WHERE user_id=:user_id"), parameters
        )
        == 0
    )
    assert (
        await db_connection.scalar(
            text(
                "SELECT count(*) FROM public.session WHERE user_id=:user_id AND revoked_at IS NULL"
            ),
            parameters,
        )
        == 1
    )
    assert (
        await db_connection.scalar(
            text("SELECT mfa_verified_at FROM public.session WHERE id=:id"), {"id": new_session}
        )
        is None
    )
    assert await db_connection.scalar(disable, parameters) is None


async def test_initial_password_consumes_only_own_fresh_code_and_revokes_other_sessions(
    db_connection: AsyncConnection, account: Account
) -> None:
    parameters = {
        "user_id": account.user_id,
        "session_id": account.session_id,
        "code_id": uuid4(),
        "hash": "e" * 64,
        "password": PASSWORD_HASH,
    }
    await db_connection.execute(
        text("UPDATE public.app_user SET password_hash=NULL WHERE id=:user_id"), parameters
    )
    await db_connection.execute(
        text(
            "INSERT INTO public.email_code(id,email_lower,code_hash,code_salt,purpose,expires_at) "
            "SELECT :code_id,email_lower,:hash,repeat('a',32),'login',now()+INTERVAL '5 minutes' "
            "FROM public.app_user WHERE id=:user_id"
        ),
        parameters,
    )
    setup = text(
        "SELECT public.set_initial_account_password(:user_id,:session_id,:code_id,:hash,:password)"
    )
    assert await db_connection.scalar(setup, {**parameters, "hash": "wrong"}) is None
    assert isinstance(await db_connection.scalar(setup, parameters), datetime)
    assert await db_connection.scalar(setup, parameters) is None
    assert await db_connection.scalar(
        text("SELECT revoked_at FROM public.session WHERE id=:id"), {"id": account.other_session_id}
    )
    assert (
        await db_connection.scalar(
            text("SELECT revoked_at FROM public.session WHERE id=:id"), {"id": account.session_id}
        )
        is None
    )
