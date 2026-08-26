"""Least-privilege contract for the isolated transactional mailer."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import (
    derive_email_outbox_encryption_key,
    email_outbox_encryption_keyring_json,
    hash_code,
)
from app.core.time import utc_now


@pytest_asyncio.fixture
async def mailer_role_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(os.environ["DATABASE_URL_MAILER"], poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def support_role_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_role_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(os.environ["DATABASE_URL_APP"], poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_mailer_can_only_claim_and_complete_outbox_delivery(
    mailer_role_engine: AsyncEngine,
) -> None:
    async with mailer_role_engine.connect() as connection:
        identity = (
            (
                await connection.execute(
                    text(
                        "SELECT session_user, current_user, "
                        "has_database_privilege(current_database(), 'CREATE') AS can_create, "
                        "has_database_privilege(current_database(), 'TEMP') AS can_temp, "
                        "(SELECT rolconnlimit FROM pg_catalog.pg_roles "
                        "WHERE rolname = current_user) AS connection_limit, "
                        "NOT EXISTS ("
                        "SELECT 1 FROM pg_catalog.pg_auth_members AS membership "
                        "JOIN pg_catalog.pg_roles AS members "
                        "ON members.oid = membership.member "
                        "WHERE members.rolname = current_user"
                        ") AS has_no_memberships"
                    )
                )
            )
            .mappings()
            .one()
        )
        direct_table_privileges = (await connection.execute(text("""
                    SELECT privilege
                    FROM (VALUES
                        ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'),
                        ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')
                    ) AS checks(privilege)
                    WHERE has_table_privilege('public.platform_email_outbox', checks.privilege)
                       OR has_table_privilege('public.auth_email_outbox', checks.privilege)
                    ORDER BY privilege
                    """))).scalars().all()
        executable_functions = (await connection.execute(text("""
                    SELECT routines.proname
                    FROM pg_catalog.pg_proc AS routines
                    JOIN pg_catalog.pg_namespace AS schemas
                      ON schemas.oid = routines.pronamespace
                    WHERE schemas.nspname = 'public'
                      AND pg_catalog.has_function_privilege(
                        routines.oid, 'EXECUTE'
                      )
                    ORDER BY routines.proname
                    """))).scalars().all()

    assert dict(identity) == {
        "session_user": "aurum_mailer",
        "current_user": "aurum_mailer",
        "can_create": False,
        "can_temp": False,
        "connection_limit": 4,
        "has_no_memberships": True,
    }
    assert direct_table_privileges == []
    assert executable_functions == [
        "claim_auth_login_email",
        "claim_platform_invitation_email",
        "complete_auth_login_email",
        "complete_platform_invitation_email",
    ]


async def test_app_can_issue_but_cannot_deliver_authentication_email(
    app_role_engine: AsyncEngine,
) -> None:
    async with app_role_engine.connect() as connection:
        privileges = (await connection.execute(text("""
                            SELECT
                              has_function_privilege(
                                'public.issue_auth_email_code('
                                'text,text,text,text,text,text,smallint,text)',
                                'EXECUTE'
                              ) AS can_issue,
                              has_function_privilege(
                                'public.claim_auth_login_email(jsonb,integer)',
                                'EXECUTE'
                              ) AS can_claim,
                              has_function_privilege(
                                'public.complete_auth_login_email(uuid,uuid,text,text)',
                                'EXECUTE'
                              ) AS can_complete,
                              has_table_privilege(
                                'public.auth_email_outbox', 'SELECT'
                              ) AS can_read_outbox
                            """))).mappings().one()

    assert dict(privileges) == {
        "can_issue": True,
        "can_claim": False,
        "can_complete": False,
        "can_read_outbox": False,
    }


async def test_issue_queues_only_known_account_and_cancels_stale_delivery(
    app_role_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    known_email = f"known-auth-{uuid4().hex}@example.invalid"
    unknown_email = f"unknown-auth-{uuid4().hex}@example.invalid"
    first_code = "123456"
    second_code = "654321"
    encryption_key = derive_email_outbox_encryption_key()

    async def issue(email: str, code: str) -> str:
        salt = uuid4().hex
        async with app_role_engine.begin() as connection:
            return str(
                await connection.scalar(
                    text("""
                        SELECT public.issue_auth_email_code(
                          :email, :code_hash, :salt, '127.0.0.1', 'pytest',
                          :code, CAST(1 AS SMALLINT), :encryption_key
                        )
                        """),
                    {
                        "email": email,
                        "code_hash": hash_code(code, salt),
                        "salt": salt,
                        "code": code,
                        "encryption_key": encryption_key,
                    },
                )
            )

    try:
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("""
                    INSERT INTO public.app_user (email, full_name, status)
                    VALUES (:email, 'Known authentication user', 'active')
                    """),
                {"email": known_email},
            )

        assert await issue(known_email, first_code) == "created"
        assert await issue(unknown_email, first_code) == "created"

        async with maintenance_engine.begin() as connection:
            deliveries = (
                (
                    await connection.execute(
                        text("""
                            SELECT code.email_lower, delivery.status,
                                   delivery.payload_ciphertext
                            FROM public.auth_email_outbox AS delivery
                            JOIN public.email_code AS code
                              ON code.id = delivery.email_code_id
                            WHERE code.email_lower IN (:known_email, :unknown_email)
                            ORDER BY delivery.created_at
                            """),
                        {
                            "known_email": known_email,
                            "unknown_email": unknown_email,
                        },
                    )
                )
                .mappings()
                .all()
            )
            assert len(deliveries) == 1
            assert deliveries[0]["email_lower"] == known_email
            assert deliveries[0]["status"] == "pending"
            assert first_code.encode() not in bytes(deliveries[0]["payload_ciphertext"])

            await connection.execute(
                text("""
                    UPDATE public.email_code
                    SET created_at = created_at - INTERVAL '2 minutes'
                    WHERE email_lower = :known_email
                    """),
                {"known_email": known_email},
            )

        assert await issue(known_email, second_code) == "created"

        async with maintenance_engine.connect() as connection:
            statuses = list(
                (
                    await connection.execute(
                        text("""
                            SELECT delivery.status
                            FROM public.auth_email_outbox AS delivery
                            JOIN public.email_code AS code
                              ON code.id = delivery.email_code_id
                            WHERE code.email_lower = :known_email
                            ORDER BY delivery.created_at
                            """),
                        {"known_email": known_email},
                    )
                ).scalars()
            )
        assert statuses == ["cancelled", "pending"]
    finally:
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM public.login_attempt WHERE email_lower IN (:known, :unknown)"),
                {"known": known_email, "unknown": unknown_email},
            )
            await connection.execute(
                text("DELETE FROM public.email_code WHERE email_lower IN (:known, :unknown)"),
                {"known": known_email, "unknown": unknown_email},
            )
            await connection.execute(
                text("DELETE FROM public.app_user WHERE email_lower = :known"),
                {"known": known_email},
            )


async def test_auth_email_claim_and_completion_clear_encrypted_code(
    mailer_role_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    code_id = uuid4()
    outbox_id = uuid4()
    email = f"auth-mailer-{uuid4().hex}@example.invalid"
    login_code = "482913"
    encryption_key = derive_email_outbox_encryption_key()

    try:
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("""
                    INSERT INTO public.email_code (
                      id, email_lower, code_hash, code_salt, purpose,
                      ip_address, expires_at
                    ) VALUES (
                      :code_id, :email, repeat('a', 64), repeat('b', 32),
                      'login', '127.0.0.1', statement_timestamp() + INTERVAL '10 minutes'
                    )
                    """),
                {"code_id": code_id, "email": email},
            )
            await connection.execute(
                text("""
                    INSERT INTO public.auth_email_outbox (
                      id, email_code_id, payload_ciphertext, encryption_key_version
                    ) VALUES (
                      :outbox_id, :code_id,
                      public.pgp_sym_encrypt(
                        :login_code, :encryption_key,
                        'cipher-algo=aes256,compress-algo=0'
                      ),
                      1
                    )
                    """),
                {
                    "outbox_id": outbox_id,
                    "code_id": code_id,
                    "login_code": login_code,
                    "encryption_key": encryption_key,
                },
            )

        async with mailer_role_engine.begin() as connection:
            claim = (
                (
                    await connection.execute(
                        text("""
                            SELECT * FROM public.claim_auth_login_email(
                              CAST(:keyring AS JSONB), 300
                            )
                            """),
                        {"keyring": email_outbox_encryption_keyring_json()},
                    )
                )
                .mappings()
                .one()
            )
            assert claim["outbox_id"] == outbox_id
            assert claim["recipient_email"] == email
            assert claim["login_code"] == login_code
            assert claim["attempt_count"] == 1
            assert (
                await connection.scalar(
                    text("""
                        SELECT public.complete_auth_login_email(
                          :outbox_id, :claim_token, 'sent', NULL
                        )
                        """),
                    {"outbox_id": outbox_id, "claim_token": claim["claim_token"]},
                )
                == "sent"
            )

        async with maintenance_engine.connect() as connection:
            terminal = (
                (
                    await connection.execute(
                        text("""
                            SELECT status, payload_ciphertext, sent_at
                            FROM public.auth_email_outbox
                            WHERE id = :outbox_id
                            """),
                        {"outbox_id": outbox_id},
                    )
                )
                .mappings()
                .one()
            )
        assert terminal["status"] == "sent"
        assert terminal["payload_ciphertext"] is None
        assert terminal["sent_at"] is not None
    finally:
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM public.email_code WHERE id = :code_id"),
                {"code_id": code_id},
            )


async def test_support_can_enqueue_but_cannot_deliver_invitation_email(
    support_role_engine: AsyncEngine,
) -> None:
    async with support_role_engine.connect() as connection:
        privileges = (await connection.execute(text("""
                    SELECT
                      has_function_privilege(
                        'public.enqueue_platform_invitation_email('
                        'uuid,uuid,uuid,integer,text,text,smallint,text)',
                        'EXECUTE'
                      ) AS can_enqueue,
                      has_function_privilege(
                        'public.claim_platform_invitation_email(jsonb,integer)',
                        'EXECUTE'
                      ) AS can_claim,
                      has_function_privilege(
                        'public.complete_platform_invitation_email('
                        'uuid,uuid,text,text)',
                        'EXECUTE'
                      ) AS can_complete,
                      has_table_privilege(
                        'public.platform_email_outbox', 'SELECT'
                      ) AS can_read_outbox
                    """))).mappings().one()

    assert dict(privileges) == {
        "can_enqueue": True,
        "can_claim": False,
        "can_complete": False,
        "can_read_outbox": False,
    }


async def test_mailer_claim_retry_and_completion_clear_encrypted_payload(
    mailer_role_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    user_id = uuid4()
    outbox_id = uuid4()
    email = f"mailer-boundary-{uuid4().hex}@example.invalid"
    activation_token = "isolated-mailer-activation-token-123456789"
    encryption_key = derive_email_outbox_encryption_key()
    expires_at = utc_now() + timedelta(hours=24)

    try:
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("""
                    INSERT INTO public.app_user (id, email, full_name, status)
                    VALUES (:user_id, :email, 'Mailer boundary', 'invited')
                    """),
                {"user_id": user_id, "email": email},
            )
            await connection.execute(
                text("""
                    INSERT INTO public.platform_staff_account (
                      user_id, status, invitation_token_hash, invitation_expires_at
                    ) VALUES (
                      :user_id, 'invited', repeat('a', 64), :expires_at
                    )
                    """),
                {"user_id": user_id, "expires_at": expires_at},
            )
            await connection.execute(
                text("""
                    INSERT INTO public.platform_email_outbox (
                      id, account_user_id, account_version, payload_ciphertext,
                      encryption_key_version
                    ) VALUES (
                      :outbox_id, :user_id, 1,
                      public.pgp_sym_encrypt(
                        :activation_token, :encryption_key,
                        'cipher-algo=aes256,compress-algo=0'
                      ),
                      1
                    )
                    """),
                {
                    "outbox_id": outbox_id,
                    "user_id": user_id,
                    "activation_token": activation_token,
                    "encryption_key": encryption_key,
                },
            )

        async with mailer_role_engine.begin() as connection:
            with pytest.raises(DBAPIError, match="encryption key version is unavailable"):
                await connection.execute(
                    text(
                        "SELECT * FROM public.claim_platform_invitation_email("
                        "CAST('{}' AS JSONB), 300)"
                    )
                )

        async with mailer_role_engine.begin() as connection:
            claim = (
                (
                    await connection.execute(
                        text("""
                        SELECT * FROM public.claim_platform_invitation_email(
                          CAST(:keyring AS JSONB), 300
                        )
                        """),
                        {"keyring": email_outbox_encryption_keyring_json()},
                    )
                )
                .mappings()
                .one()
            )
            assert claim["outbox_id"] == outbox_id
            assert claim["recipient_email"] == email
            assert claim["activation_token"] == activation_token
            assert claim["attempt_count"] == 1
            assert (
                await connection.scalar(
                    text("""
                        SELECT public.complete_platform_invitation_email(
                          :outbox_id, :claim_token, 'transient_failure', 'smtp_unavailable'
                        )
                        """),
                    {"outbox_id": outbox_id, "claim_token": claim["claim_token"]},
                )
                == "pending"
            )

        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("""
                    UPDATE public.platform_email_outbox
                    SET available_at = statement_timestamp()
                    WHERE id = :outbox_id
                    """),
                {"outbox_id": outbox_id},
            )

        async with mailer_role_engine.begin() as connection:
            retry = (
                (
                    await connection.execute(
                        text("""
                        SELECT * FROM public.claim_platform_invitation_email(
                          CAST(:keyring AS JSONB), 300
                        )
                        """),
                        {"keyring": email_outbox_encryption_keyring_json()},
                    )
                )
                .mappings()
                .one()
            )
            assert retry["attempt_count"] == 2
            assert (
                await connection.scalar(
                    text("""
                        SELECT public.complete_platform_invitation_email(
                          :outbox_id, :claim_token, 'sent', NULL
                        )
                        """),
                    {"outbox_id": outbox_id, "claim_token": retry["claim_token"]},
                )
                == "sent"
            )

        async with maintenance_engine.connect() as connection:
            terminal = (
                (
                    await connection.execute(
                        text("""
                        SELECT status, attempt_count, payload_ciphertext, sent_at
                        FROM public.platform_email_outbox
                        WHERE id = :outbox_id
                        """),
                        {"outbox_id": outbox_id},
                    )
                )
                .mappings()
                .one()
            )
        assert terminal["status"] == "sent"
        assert terminal["attempt_count"] == 2
        assert terminal["payload_ciphertext"] is None
        assert terminal["sent_at"] is not None
    finally:
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM public.platform_email_outbox WHERE id = :outbox_id"),
                {"outbox_id": outbox_id},
            )
            await connection.execute(
                text("DELETE FROM public.platform_staff_account WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            await connection.execute(
                text("DELETE FROM public.app_user WHERE id = :user_id"),
                {"user_id": user_id},
            )
