"""Security primitives used by support-account MFA."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

import pytest
from pydantic import SecretStr

import app.core.security as security_module
from app.core.security import (
    build_totp_uri,
    create_access_token,
    decode_access_token,
    generate_recovery_codes,
    generate_totp_secret,
    hash_recovery_code,
    match_totp_counter,
    mfa_encryption_keyring_json,
)


@pytest.mark.parametrize(
    ("unix_time", "six_digit_code"),
    [
        (59, "287082"),
        (1_111_111_109, "081804"),
        (1_111_111_111, "050471"),
        (1_234_567_890, "005924"),
        (2_000_000_000, "279037"),
        (20_000_000_000, "353130"),
    ],
)
def test_totp_matches_rfc_6238_sha1_vectors(
    unix_time: int,
    six_digit_code: str,
) -> None:
    secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
    instant = datetime.fromtimestamp(unix_time, tz=UTC)

    counter = match_totp_counter(
        secret,
        six_digit_code,
        at=instant,
        window=0,
    )

    assert counter == unix_time // 30
    assert (
        match_totp_counter(
            secret,
            six_digit_code,
            at=instant,
            last_used_counter=counter,
            window=0,
        )
        is None
    )


def test_totp_rejects_malformed_codes_and_oversized_window() -> None:
    secret = generate_totp_secret()

    assert match_totp_counter(secret, "12345") is None
    assert match_totp_counter(secret, "１２３４５６") is None
    with pytest.raises(ValueError, match="window"):
        match_totp_counter(secret, "123456", window=3)


def test_generated_secret_and_recovery_codes_are_strong_and_unique() -> None:
    secret = generate_totp_secret()
    recovery_codes = generate_recovery_codes()

    assert len(secret) == 32
    assert set(secret) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
    assert len(recovery_codes) == 10
    assert len(set(recovery_codes)) == 10
    assert all(len(code.replace("-", "")) == 20 for code in recovery_codes)
    assert len({hash_recovery_code(code) for code in recovery_codes}) == 10


def test_recovery_code_hash_is_normalized_and_independent_from_jwt_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code = "ABCDE-FGHIJ-KLMNO-P2345"

    digest = hash_recovery_code(code)
    monkeypatch.setattr(security_module.settings, "JWT_SECRET", "rotated-" + "z" * 40)

    assert digest == hash_recovery_code("abcde fghij klmno p2345")
    assert digest != code.replace("-", "")
    with pytest.raises(ValueError, match="Invalid recovery code"):
        hash_recovery_code("not-a-recovery-code")


def test_mfa_keyring_contains_current_and_previous_derived_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        security_module.settings,
        "MFA_ENCRYPTION_KEY",
        SecretStr("current-" + "c" * 40),
    )
    monkeypatch.setattr(security_module.settings, "MFA_ENCRYPTION_KEY_VERSION", 2)
    monkeypatch.setattr(
        security_module.settings,
        "MFA_ENCRYPTION_PREVIOUS_KEYS",
        {1: SecretStr("previous-" + "p" * 40)},
    )

    keyring = json.loads(mfa_encryption_keyring_json())

    assert sorted(keyring) == ["1", "2"]
    assert keyring["1"] != keyring["2"]
    assert all(len(value) == 64 for value in keyring.values())


def test_totp_uri_encodes_account_and_declares_parameters() -> None:
    secret = generate_totp_secret()
    uri = build_totp_uri(
        account_name="admin+test@aurum.tj",
        secret=secret,
        issuer="Aurum Pharma",
    )
    parsed = urlparse(uri)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "otpauth"
    assert parsed.netloc == "totp"
    assert unquote(parsed.path) == "/Aurum Pharma:admin+test@aurum.tj"
    assert query == {
        "secret": [secret],
        "issuer": ["Aurum Pharma"],
        "algorithm": ["SHA1"],
        "digits": ["6"],
        "period": ["30"],
    }


def test_support_access_token_contains_session_and_mfa_time_not_permissions() -> None:
    user_id = uuid4()
    session_id = uuid4()
    verified_at = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)

    claims = decode_access_token(
        create_access_token(
            user_id,
            tenant_id=None,
            is_developer=True,
            is_administrator=False,
            session_id=session_id,
            mfa_verified_at=verified_at,
        )
    )

    assert claims["sub"] == str(user_id)
    assert claims["sid"] == str(session_id)
    assert claims["mfa_at"] == int(verified_at.timestamp())
    assert "permissions" not in claims
