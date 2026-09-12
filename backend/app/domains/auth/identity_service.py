"""Email/password and verified-phone login, recovery and identity settings."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import cast
from uuid import UUID

from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.errors import (
    AuthenticationError,
    ConflictError,
    RateLimitError,
    ServiceUnavailableError,
)
from app.core.security import (
    derive_email_outbox_encryption_key,
    generate_code_salt,
    generate_email_code,
    generate_refresh_token,
    hash_code,
    hash_password,
    hash_recovery_code,
    hash_token,
    match_totp_counter,
    mfa_encryption_keyring_json,
    verify_password,
)
from app.core.sms import send_sms_code
from app.domains.auth.identity_repository import IdentityRepository
from app.domains.auth.service import AuthService, AuthTokens, MfaLoginChallenge

_DUMMY_PASSWORD_HASH = hash_password(generate_refresh_token())


class IdentityService(AuthService):
    repo: IdentityRepository

    async def _budget(self, key: str, limit: int, seconds: int) -> None:
        if self.redis is None:
            raise ServiceUnavailableError("Authentication attempt guard unavailable")
        try:
            pending = self.redis.eval(
                "local n=redis.call('INCR',KEYS[1]); "
                "if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]); end; return n",
                1,
                f"auth:identity:{key}",
                str(seconds),
            )
            count = await cast(Awaitable[str], pending)
        except RedisError as exc:
            raise ServiceUnavailableError("Authentication attempt guard unavailable") from exc
        if int(count) > limit:
            raise RateLimitError("Too many requests. Try again later.")

    async def identity_settings(self, user_id: UUID, session_id: UUID) -> dict[str, object]:
        record = await self.repo.identity(user_id, session_id)
        if record is None:
            raise AuthenticationError("Authenticated session is inactive")
        return record

    async def request_code(
        self,
        *,
        channel: str,
        contact: str,
        purpose: str,
        ip: str,
        agent: str | None,
        user_id: UUID | None = None,
        session_id: UUID | None = None,
        mfa_counter: int | None = None,
        mfa_secret: str | None = None,
    ) -> str | None:
        config = get_settings()
        if channel == "phone" and config.SMS_PROVIDER == "disabled":
            raise ServiceUnavailableError("SMS login is not configured")
        await self._budget(f"issue:ip:{hash_token(ip)}", 30, 3600)
        await self._budget(f"issue:contact:{hash_token(channel+contact)}", 10, 3600)
        code, salt = generate_email_code(), generate_code_salt()
        result = await self.repo.issue_identity_code(
            {
                "channel": channel,
                "contact": contact,
                "purpose": purpose,
                "hash": hash_code(code, salt),
                "salt": salt,
                "user": user_id,
                "session": session_id,
                "ip": ip,
                "agent": agent,
                "plaintext": code,
                "key_version": config.EMAIL_OUTBOX_ENCRYPTION_KEY_VERSION,
                "key": derive_email_outbox_encryption_key(),
                "mfa_counter": mfa_counter,
                "mfa_secret": mfa_secret,
                "mfa_keyring": mfa_encryption_keyring_json(),
            }
        )
        if result is None:
            raise AuthenticationError("Confirm account identity first")
        if result["status"] != "created":
            raise RateLimitError("Too many code requests. Try again later.")
        if channel == "phone" and result.get("deliver"):
            try:
                await self._budget("sms:daily", config.SMS_DAILY_LIMIT, 86400)
                await send_sms_code(contact, code)
            except (RateLimitError, ServiceUnavailableError):
                # Public requests must not reveal whether the destination belongs
                # to an account when delivery fails or the paid budget is spent.
                # Keep the challenge: an uncertain send may already have arrived.
                if purpose not in {"login", "reset"}:
                    raise
        if config.ENVIRONMENT == "development" and (
            channel == "email" or config.SMS_PROVIDER == "development"
        ):
            return code
        return None

    async def _check_code(
        self,
        *,
        channel: str,
        contact: str,
        purpose: str,
        code: str,
        ip: str,
        session_id: UUID | None = None,
    ) -> tuple[UUID, str, UUID]:
        await self._budget(f"verify:ip:{hash_token(ip)}", 50, 900)
        await self._budget(f"verify:contact:{hash_token(channel+contact)}", 5, 300)
        row = await self.repo.find_identity_code(channel, contact, purpose, session_id)
        if row is None:
            raise AuthenticationError("Invalid or expired code")
        code_id = UUID(str(row["id"]))
        candidate = hash_code(code, str(row["salt"]))
        checked = await self.repo.check_identity_code(code_id, candidate, session_id)
        if checked is None:
            raise AuthenticationError("Invalid or expired code")
        return code_id, candidate, UUID(str(checked["user_id"]))

    async def login_password(
        self, *, email: str, password: str, ip: str, agent: str | None, device_id: str
    ) -> AuthTokens | MfaLoginChallenge:
        email = email.strip().lower()
        await self._budget(f"password:ip:{hash_token(ip)}", 50, 900)
        await self._budget(f"password:email:{hash_token(email)}", 5, 900)
        if await self._login_is_blocked(email_lower=email, ip_address=ip):
            raise RateLimitError("Account temporarily locked")
        stored = await self.repo.password_hash_for_login(email)
        try:
            valid = verify_password(password, stored or _DUMMY_PASSWORD_HASH)
        except ValueError:
            valid = False
        if not stored or not valid:
            await self.repo.insert_login_attempt(
                email_lower=email,
                user_id=None,
                ip_address=ip,
                user_agent=agent,
                outcome="password_failed",
            )
            raise AuthenticationError("Invalid credentials")
        if self.redis is not None:
            await self.redis.delete(f"auth:identity:password:email:{hash_token(email)}")
        return await self._login(
            email=email,
            password_hash=stored,
            code_id=None,
            candidate=None,
            ip=ip,
            agent=agent,
            device_id=device_id,
        )

    async def login_phone(
        self, *, phone: str, code: str, ip: str, agent: str | None, device_id: str
    ) -> AuthTokens | MfaLoginChallenge:
        code_id, candidate, _ = await self._check_code(
            channel="phone", contact=phone, purpose="login", code=code, ip=ip
        )
        return await self._login(
            email=None,
            password_hash=None,
            code_id=code_id,
            candidate=candidate,
            ip=ip,
            agent=agent,
            device_id=device_id,
        )

    async def _login(
        self,
        *,
        email: str | None,
        password_hash: str | None,
        code_id: UUID | None,
        candidate: str | None,
        ip: str,
        agent: str | None,
        device_id: str,
    ) -> AuthTokens | MfaLoginChallenge:
        salt = generate_code_salt()
        proof_hash = hash_code(generate_refresh_token(), salt)
        proof = await self.repo.login_proof(
            email=email,
            password_hash=password_hash,
            code_id=code_id,
            candidate=candidate,
            proof_hash=proof_hash,
            salt=salt,
            ip=ip,
        )
        if proof is None:
            raise AuthenticationError("Invalid credentials")
        proof_id = UUID(str(proof["id"]))
        user = await self.repo.get_login_user_by_email(
            email=str(proof["email"]), code_id=proof_id, candidate_hash=proof_hash
        )
        if user is None:
            raise AuthenticationError("Invalid credentials")
        return await self._complete_login_proof(
            user=user,
            code_id=proof_id,
            candidate_hash=proof_hash,
            ip_address=ip,
            user_agent=agent,
            device_id=device_id,
        )

    async def change_password(
        self,
        *,
        channel: str,
        contact: str,
        code: str,
        new_password: str,
        ip: str,
        purpose: str = "reset",
        session_id: UUID | None = None,
        mfa_code: str | None = None,
        recovery_code: str | None = None,
    ) -> None:
        code_id, candidate, user_id = await self._check_code(
            channel=channel,
            contact=contact,
            purpose=purpose,
            code=code,
            ip=ip,
            session_id=session_id,
        )
        factor = await self.repo.factor(
            code_id, candidate, session_id, mfa_encryption_keyring_json()
        )
        if factor is None:
            raise AuthenticationError("Invalid or expired code")
        counter: int | None = None
        secret = cast(str | None, factor.get("secret"))
        recovery_hash: str | None = None
        if factor.get("required") and purpose == "reset":
            if not mfa_code and not recovery_code:
                raise AuthenticationError(
                    "Enter your authenticator or recovery code", details={"reason": "mfa_required"}
                )
            await self._claim_mfa_attempt(user_id)
            if recovery_code:
                try:
                    recovery_hash = hash_recovery_code(recovery_code)
                except ValueError as exc:
                    raise AuthenticationError("Invalid authentication code") from exc
            elif secret and mfa_code:
                counter = match_totp_counter(
                    secret, mfa_code, last_used_counter=cast(int | None, factor.get("counter"))
                )
                if counter is None:
                    raise AuthenticationError("Invalid authentication code")
            else:
                raise AuthenticationError("Use an MFA recovery code")
        changed = await self.repo.finish_password(
            {
                "id": code_id,
                "hash": candidate,
                "session": session_id,
                "password": hash_password(new_password),
                "counter": counter,
                "secret": secret,
                "keyring": mfa_encryption_keyring_json(),
                "recovery": recovery_hash,
            }
        )
        if not changed:
            raise AuthenticationError("Invalid or expired confirmation")

    async def request_setup(
        self, *, user_id: UUID, session_id: UUID, channel: str, ip: str, agent: str | None
    ) -> str | None:
        identity = await self.identity_settings(user_id, session_id)
        if identity["has_password"]:
            raise ConflictError("Account password already configured")
        contact = identity["email"] if channel == "email" else identity["phone"]
        if not contact:
            raise AuthenticationError("Verified contact unavailable")
        return await self.request_code(
            channel=channel,
            contact=str(contact),
            purpose="setup",
            ip=ip,
            agent=agent,
            user_id=user_id,
            session_id=session_id,
        )

    async def request_phone_binding(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        phone: str,
        password: str | None,
        reauth_code: str | None,
        mfa_code: str | None,
        ip: str,
        agent: str | None,
    ) -> str | None:
        identity = await self.identity_settings(user_id, session_id)
        if identity["has_password"]:
            if not password:
                raise AuthenticationError("Enter your current password")
            await self.confirm_account_password(
                user_id=user_id,
                session_id=session_id,
                password=password,
                ip_address=ip,
                user_agent=agent,
            )
        else:
            if not reauth_code:
                raise AuthenticationError("Confirm your existing email")
            code_id, candidate, _ = await self._check_code(
                channel="email",
                contact=str(identity["email"]),
                purpose="reauth",
                code=reauth_code,
                ip=ip,
                session_id=session_id,
            )
            if not await self.repo.consume_reauth(code_id, candidate, user_id, session_id):
                raise AuthenticationError("Invalid or expired confirmation")
        actor = await self.repo.get_user_by_id(user_id, session_id=session_id)
        if actor is None:
            raise AuthenticationError("Authenticated session inactive")
        counter: int | None = None
        secret: str | None = None
        if actor.mfa_required:
            if not mfa_code:
                raise AuthenticationError(
                    "Enter your authenticator code", details={"reason": "mfa_required"}
                )
            await self._claim_mfa_attempt(user_id)
            factor = await self.repo.get_step_up_mfa(
                user_id=user_id,
                session_id=session_id,
                encryption_keyring=mfa_encryption_keyring_json(),
            )
            if factor is None:
                raise AuthenticationError("Account MFA unavailable")
            secret = factor.secret
            counter = match_totp_counter(
                secret, mfa_code, last_used_counter=factor.last_used_counter
            )
            if counter is None:
                raise AuthenticationError("Invalid authentication code")
        return await self.request_code(
            channel="phone",
            contact=phone,
            purpose="bind",
            ip=ip,
            agent=agent,
            user_id=user_id,
            session_id=session_id,
            mfa_counter=counter,
            mfa_secret=secret,
        )

    async def bind_phone(
        self, *, user_id: UUID, session_id: UUID, phone: str, code: str, ip: str
    ) -> None:
        code_id, candidate, _ = await self._check_code(
            channel="phone", contact=phone, purpose="bind", code=code, ip=ip, session_id=session_id
        )
        if not await self.repo.finish_phone(code_id, candidate, user_id, session_id):
            raise AuthenticationError("Phone confirmation is unavailable")
