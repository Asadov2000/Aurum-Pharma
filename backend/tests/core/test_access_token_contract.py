"""Signed tokens must satisfy one explicit access profile before middleware trusts them."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.core import security
from app.core.errors import AuthenticationError
from app.middleware.auth_context import AuthContextMiddleware


def _claims() -> dict[str, object]:
    now = int(security.utc_now().timestamp())
    return {
        "sub": str(uuid4()),
        "sid": str(uuid4()),
        "tenant_id": str(uuid4()),
        "is_developer": False,
        "is_administrator": False,
        "mfa_at": None,
        "password_at": None,
        "iat": now,
        "exp": now + 900,
        "iss": security.settings.JWT_ISSUER,
        "aud": security.settings.JWT_AUDIENCE,
        "jti": str(uuid4()),
    }


def _sign(claims: dict[str, object], *, token_type: str | None = security.ACCESS_TOKEN_TYPE) -> str:
    # Sign raw JSON to test malformed claims rejected by PyJWT's normal issuer.
    return jwt.api_jws.encode(
        json.dumps(claims).encode(),
        security.settings.JWT_SECRET,
        algorithm=security.settings.JWT_ALGORITHM,
        headers={"typ": token_type},
    )


def _legacy_claims() -> dict[str, object]:
    return {key: value for key, value in _claims().items() if key not in {"iss", "aud", "jti"}}


def test_factory_issues_unique_typed_access_tokens_without_permissions() -> None:
    user_id, session_id, tenant_id = uuid4(), uuid4(), uuid4()
    tokens = [
        security.create_access_token(
            user_id,
            session_id=session_id,
            tenant_id=tenant_id,
            is_developer=False,
            is_administrator=False,
        )
        for _ in range(2)
    ]
    first, second = (security.decode_access_token(token) for token in tokens)
    assert first["jti"] != second["jti"]
    assert first["sub"] == second["sub"] == str(user_id)
    assert first["sid"] == second["sid"] == str(session_id)
    assert first["tenant_id"] == str(tenant_id)
    assert first["iss"] == security.settings.JWT_ISSUER
    assert first["aud"] == security.settings.JWT_AUDIENCE
    assert "permissions" not in first
    assert jwt.get_unverified_header(tokens[0])["typ"] == security.ACCESS_TOKEN_TYPE


@pytest.mark.parametrize(
    "claim",
    [
        "sub",
        "sid",
        "iat",
        "exp",
        "iss",
        "aud",
        "jti",
        "tenant_id",
        "is_developer",
        "is_administrator",
    ],
)
def test_missing_required_claim_is_rejected(claim: str) -> None:
    claims = _claims()
    del claims[claim]
    with pytest.raises(AuthenticationError):
        security.decode_access_token(_sign(claims))


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("iss", "another-issuer"),
        ("iss", None),
        ("aud", "another-api"),
        ("aud", ["aurum-pharma-api"]),
        ("aud", None),
        ("jti", ""),
        ("jti", 123),
        ("sub", "not-a-uuid"),
        ("sid", []),
        ("tenant_id", {}),
        ("is_developer", "false"),
        ("is_administrator", 1),
        ("iat", True),
        ("iat", []),
        ("exp", {}),
        ("exp", float("inf")),
        ("nbf", None),
        ("exp", "9999999999"),
        ("mfa_at", True),
        ("mfa_at", 10**100),
        ("password_at", -1),
        ("permissions", ["sales.create"]),
    ],
)
def test_malformed_signed_claims_are_rejected(claim: str, value: object) -> None:
    claims = _claims()
    claims[claim] = value
    with pytest.raises(AuthenticationError):
        security.decode_access_token(_sign(claims))


@pytest.mark.parametrize("token_type", [None, "JWT", "refresh+jwt", "at+jwt"])
def test_wrong_or_missing_signed_type_is_rejected(token_type: str | None) -> None:
    with pytest.raises(AuthenticationError):
        security.decode_access_token(_sign(_claims(), token_type=token_type))


@pytest.mark.parametrize("claim", ["sub", "iat", "exp", "iss", "aud", "jti", "permissions"])
def test_extra_cannot_replace_contract_fields(claim: str) -> None:
    with pytest.raises(ValueError, match="Reserved access-token claims"):
        security.encode_token(
            str(uuid4()), expires_in=timedelta(minutes=15), extra={claim: "override"}
        )


def test_signature_algorithm_and_lifetime_remain_enforced() -> None:
    claims = _claims()
    invalid_tokens = [
        jwt.encode(
            claims,
            "different-test-key-" * 3,
            algorithm="HS256",
            headers={"typ": security.ACCESS_TOKEN_TYPE},
        ),
        jwt.encode(claims, "", algorithm="none", headers={"typ": security.ACCESS_TOKEN_TYPE}),
        _sign({**claims, "exp": int(claims["iat"]) + 901}),
        _sign({**claims, "exp": int(claims["iat"]) - 1}),
        _sign({**claims, "iat": int(claims["iat"]) + 60}),
    ]
    for token in invalid_tokens:
        with pytest.raises(AuthenticationError):
            security.decode_access_token(token)


def test_legacy_window_expires_without_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    now = int(security.utc_now().timestamp())
    deadline = now + 60
    monkeypatch.setattr(security.settings, "JWT_LEGACY_ACCESS_UNTIL", deadline)
    token = _sign(_legacy_claims(), token_type="JWT")
    monkeypatch.setattr(security, "utc_now", lambda: datetime.fromtimestamp(deadline - 1, UTC))
    assert security.decode_access_token(token)["sid"]
    for timestamp in (deadline, deadline + 1):
        monkeypatch.setattr(
            security, "utc_now", lambda timestamp=timestamp: datetime.fromtimestamp(timestamp, UTC)
        )
        with pytest.raises(AuthenticationError):
            security.decode_access_token(token)


@pytest.mark.parametrize("claim", ["iss", "aud", "jti"])
@pytest.mark.parametrize("value", [None, ""])
def test_partial_new_profile_never_falls_back_to_legacy(
    monkeypatch: pytest.MonkeyPatch, claim: str, value: object
) -> None:
    monkeypatch.setattr(
        security.settings, "JWT_LEGACY_ACCESS_UNTIL", int(security.utc_now().timestamp()) + 60
    )
    claims = _legacy_claims()
    claims[claim] = value
    with pytest.raises(AuthenticationError):
        security.decode_access_token(_sign(claims, token_type="JWT"))


def test_legacy_checks_identity_type_and_fixed_maximum_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        security.settings, "JWT_LEGACY_ACCESS_UNTIL", int(security.utc_now().timestamp()) + 60
    )
    monkeypatch.setattr(security.settings, "ACCESS_TOKEN_MINUTES", 30)
    claims = _legacy_claims()
    for invalid in (
        {**claims, "exp": int(claims["iat"]) + 901},
        {**claims, "is_developer": "false"},
        {**claims, "sid": "invalid"},
    ):
        with pytest.raises(AuthenticationError):
            security.decode_access_token(_sign(invalid, token_type="JWT"))
    with pytest.raises(AuthenticationError):
        security.decode_access_token(_sign(claims))


@pytest.mark.parametrize("legacy", [False, True])
async def test_invalid_signed_role_never_selects_support_pool(
    monkeypatch: pytest.MonkeyPatch, legacy: bool
) -> None:
    monkeypatch.setattr(
        security.settings, "JWT_LEGACY_ACCESS_UNTIL", int(security.utc_now().timestamp()) + 60
    )
    app = FastAPI()
    app.add_middleware(AuthContextMiddleware)

    @app.get("/api/v1/admin/probe")
    async def probe(request: Request) -> dict[str, bool]:
        return {
            "support": bool(getattr(request.state, "use_support_pool", False)),
            "identity": getattr(request.state, "user_id", None) is not None,
        }

    claims = _legacy_claims() if legacy else _claims()
    claims["is_developer"] = "false"
    token = _sign(claims, token_type="JWT" if legacy else security.ACCESS_TOKEN_TYPE)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/admin/probe", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.json() == {"support": False, "identity": False}
