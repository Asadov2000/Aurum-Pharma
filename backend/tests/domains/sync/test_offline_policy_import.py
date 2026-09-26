"""Operator import rejects untrusted configuration and bounded files before SQL."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domains.sync import offline_policy_import as command
from tests.domains.sync.offline_policy_fixtures import SigningFixture, binding, scope, uuid


def _configuration(signing: SigningFixture) -> dict[str, object]:
    return {
        "expected_scope": scope().model_dump(mode="json"),
        "expected_device": binding().model_dump(mode="json"),
        "policy_keys": [
            {"key_id": str(key_id), "public_key": key.hex()}
            for key_id, key in signing.policy_keys.items()
        ],
        "lease_keys": [
            {"key_id": str(key_id), "public_key": key.hex()}
            for key_id, key in signing.lease_keys.items()
        ],
    }


@pytest.mark.parametrize("duplicate", ["identifier", "material"])
def test_trusted_key_aliases_cannot_be_hidden_by_mapping_conversion(duplicate: str) -> None:
    value = _configuration(SigningFixture())
    keys = value["policy_keys"]
    assert isinstance(keys, list)
    second = dict(keys[0])
    if duplicate == "material":
        second["key_id"] = str(uuid(99))
    keys.append(second)
    configuration = command._TrustedImportConfiguration.model_validate_json(json.dumps(value))
    with pytest.raises(ValueError):
        configuration.verifier()


def test_trusted_config_forbids_embedded_secrets_or_implicit_lease_trust() -> None:
    value = _configuration(SigningFixture())
    value["private_seed"] = "synthetic-private-value"
    with pytest.raises(ValidationError):
        command._TrustedImportConfiguration.model_validate_json(json.dumps(value))
    del value["private_seed"]
    del value["lease_keys"]
    with pytest.raises(ValidationError):
        command._TrustedImportConfiguration.model_validate_json(json.dumps(value))


@pytest.mark.parametrize("size", [65_536, 65_537])
def test_import_file_read_has_exact_bound(tmp_path: Path, size: int) -> None:
    source = tmp_path / "policy.json"
    source.write_bytes(b"x" * size)
    if size == 65_536:
        assert len(command._bounded_read(source)) == size
    else:
        with pytest.raises(ValueError):
            command._bounded_read(source)


def test_import_error_does_not_print_payload_paths_or_private_diagnostics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "fixture-private.json"
    source.write_text('{"secret":"synthetic-private-value"}', encoding="utf-8")
    assert command.main(["--delivery", str(source), "--trusted-config", str(source)]) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == "Policy delivery import failed; no offline authority was granted.\n"
    assert "synthetic-private-value" not in captured.out
    assert str(source) not in captured.out
