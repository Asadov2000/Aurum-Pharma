"""Regression coverage for test infrastructure isolation and teardown failures."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from fnmatch import fnmatchcase
from unittest.mock import AsyncMock, Mock

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from tests.fixture_helpers import (
    clear_test_auth_redis,
    eager_tasks,
    resolve_test_redis_url,
)


def test_test_redis_never_inherits_application_endpoint() -> None:
    assert resolve_test_redis_url({"REDIS_URL": "redis://shared-dev:6379/0"}) == (
        "redis://redis-test:6379/0"
    )


def test_test_redis_accepts_explicit_dedicated_endpoint() -> None:
    assert resolve_test_redis_url({"TEST_REDIS_URL": "redis://localhost:6380/2"}) == (
        "redis://localhost:6380/2"
    )


@pytest.mark.parametrize("url", ["", "https://example.test", "redis:///0"])
def test_test_redis_rejects_invalid_endpoint(url: str) -> None:
    with pytest.raises(RuntimeError, match="TEST_REDIS_URL"):
        resolve_test_redis_url({"TEST_REDIS_URL": url})


async def test_auth_cleanup_clears_all_guard_keys_and_preserves_unrelated_keys() -> None:
    keys = {
        "auth:perms:account",
        "auth:mfa-attempts:account",
        "auth:password-confirmation:account",
        "other:state",
        "auth:unrelated:account",
    }
    client = Mock(spec=Redis)

    async def scan_iter(*, match: str) -> AsyncIterator[str]:
        for key in tuple(keys):
            if fnmatchcase(key, match):
                yield key

    async def delete(key: str) -> int:
        keys.remove(key)
        return 1

    client.scan_iter = scan_iter
    client.delete = AsyncMock(side_effect=delete)

    await clear_test_auth_redis(client)

    assert keys == {"other:state", "auth:unrelated:account"}


@pytest.mark.parametrize("failure_stage", ["scan", "delete"])
async def test_auth_cleanup_reports_redis_failure_without_connection_details(
    failure_stage: str,
) -> None:
    client = Mock(spec=Redis)
    sensitive_detail = "credential-bearing-connection-details"

    async def scan_iter(*, match: str) -> AsyncIterator[str]:
        if failure_stage == "scan":
            raise RedisConnectionError(sensitive_detail)
        yield "auth:perms:account"

    client.scan_iter = scan_iter
    client.delete = AsyncMock(side_effect=RedisConnectionError(sensitive_detail))

    with pytest.raises(RuntimeError, match="TEST_REDIS_URL") as captured:
        await clear_test_auth_redis(client)

    assert sensitive_detail not in str(captured.value)
    assert captured.value.__suppress_context__


@dataclass
class _TaskConfig:
    task_always_eager: bool
    task_eager_propagates: bool


@pytest.mark.parametrize("eager,propagates", [(False, False), (True, False), (False, True)])
@pytest.mark.parametrize("body_fails", [False, True])
def test_celery_eager_restores_both_flags(eager: bool, propagates: bool, body_fails: bool) -> None:
    config = _TaskConfig(eager, propagates)

    def run_body() -> None:
        with eager_tasks(config):
            assert config.task_always_eager
            assert config.task_eager_propagates
            if body_fails:
                raise ValueError("test body failed")

    if body_fails:
        with pytest.raises(ValueError, match="test body failed"):
            run_body()
    else:
        run_body()

    assert config == _TaskConfig(eager, propagates)
