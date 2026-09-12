"""Proof-scoped database operations for account login identities."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import text

from app.domains.auth.repository import AuthRepository


class IdentityRepository(AuthRepository):
    async def _json(self, sql: str, values: dict[str, object]) -> dict[str, object] | None:
        result = await self.session.scalar(text(sql), values)
        return cast(dict[str, object] | None, result)

    async def password_hash_for_login(self, email: str) -> str | None:
        return cast(
            str | None,
            await self.session.scalar(
                text("SELECT public.lookup_identity_password(:email)"), {"email": email}
            ),
        )

    async def identity(self, user_id: UUID, session_id: UUID) -> dict[str, object] | None:
        return await self._json(
            "SELECT public.lookup_account_identity(:user,:session)",
            {"user": user_id, "session": session_id},
        )

    async def dismiss_password_prompt(self, user_id: UUID, session_id: UUID) -> bool:
        return bool(
            await self.session.scalar(
                text("SELECT public.dismiss_account_password_prompt(:user,:session)"),
                {"user": user_id, "session": session_id},
            )
        )

    async def issue_identity_code(self, values: dict[str, object]) -> dict[str, object] | None:
        return await self._json(
            """SELECT public.issue_identity_code(:channel,:contact,:purpose,
        :hash,:salt,:user,:session,:ip,:agent,:plaintext,CAST(:key_version AS SMALLINT),:key,
        :mfa_counter,:mfa_secret,CAST(:mfa_keyring AS JSONB))""",
            values,
        )

    async def find_identity_code(
        self, channel: str, contact: str, purpose: str, session_id: UUID | None
    ) -> dict[str, object] | None:
        return await self._json(
            "SELECT public.find_identity_code(:channel,:contact,:purpose,:session)",
            {"channel": channel, "contact": contact, "purpose": purpose, "session": session_id},
        )

    async def check_identity_code(
        self, code_id: UUID, candidate: str, session_id: UUID | None
    ) -> dict[str, object] | None:
        return await self._json(
            "SELECT public.check_identity_code(:id,:hash,:session)",
            {"id": code_id, "hash": candidate, "session": session_id},
        )

    async def login_proof(
        self,
        *,
        email: str | None,
        password_hash: str | None,
        code_id: UUID | None,
        candidate: str | None,
        proof_hash: str,
        salt: str,
        ip: str,
    ) -> dict[str, object] | None:
        return await self._json(
            "SELECT public.create_identity_login_proof("
            ":email,:password,:code,:candidate,:hash,:salt,:ip)",
            {
                "email": email,
                "password": password_hash,
                "code": code_id,
                "candidate": candidate,
                "hash": proof_hash,
                "salt": salt,
                "ip": ip,
            },
        )

    async def factor(
        self, code_id: UUID, candidate: str, session_id: UUID | None, keyring: str
    ) -> dict[str, object] | None:
        return await self._json(
            "SELECT public.lookup_identity_factor(:id,:hash,:session,CAST(:keyring AS JSONB))",
            {"id": code_id, "hash": candidate, "session": session_id, "keyring": keyring},
        )

    async def finish_password(self, values: dict[str, object]) -> bool:
        return bool(
            await self.session.scalar(
                text("""SELECT public.complete_identity_password(
        :id,:hash,:session,:password,:counter,:secret,CAST(:keyring AS JSONB),:recovery)"""),
                values,
            )
        )

    async def consume_reauth(
        self, code_id: UUID, candidate: str, user_id: UUID, session_id: UUID
    ) -> bool:
        return bool(
            await self.session.scalar(
                text("SELECT public.consume_identity_reauth(:id,:hash,:user,:session)"),
                {"id": code_id, "hash": candidate, "user": user_id, "session": session_id},
            )
        )

    async def finish_phone(
        self, code_id: UUID, candidate: str, user_id: UUID, session_id: UUID
    ) -> bool:
        return bool(
            await self.session.scalar(
                text("SELECT public.complete_identity_phone(:id,:hash,:user,:session)"),
                {"id": code_id, "hash": candidate, "user": user_id, "session": session_id},
            )
        )
