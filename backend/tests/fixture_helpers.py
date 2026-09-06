"""State management for test fixtures; never reuse application Redis implicitly."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Protocol
from urllib.parse import urlparse

from redis.asyncio import Redis
from redis.exceptions import RedisError

_AUTH_REDIS_PATTERNS = (
    "auth:perms:*",
    "auth:mfa-attempts:*",
    "auth:password-confirmation:*",
)


def resolve_test_redis_url(environ: Mapping[str, str]) -> str:
    url = environ.get("TEST_REDIS_URL", "redis://redis-test:6379/0")
    try:
        parsed = urlparse(url)
        valid = parsed.scheme in {"redis", "rediss"} and bool(parsed.hostname)
    except ValueError:
        valid = False
    if not valid:
        raise RuntimeError("TEST_REDIS_URL must identify a dedicated test Redis instance")
    return url


async def clear_test_auth_redis(client: Redis) -> None:
    """Fail setup when isolated Redis cannot be cleaned instead of hiding state leaks."""
    try:
        for pattern in _AUTH_REDIS_PATTERNS:
            async for key in client.scan_iter(match=pattern):
                await client.delete(key)
    except RedisError:
        raise RuntimeError(
            "Cannot clear test auth state; check that TEST_REDIS_URL points to an "
            "available dedicated test Redis instance"
        ) from None


class EagerTaskConfig(Protocol):
    task_always_eager: bool
    task_eager_propagates: bool


@contextmanager
def eager_tasks(config: EagerTaskConfig) -> Iterator[None]:
    previous_eager = config.task_always_eager
    previous_propagates = config.task_eager_propagates
    try:
        config.task_always_eager = True
        config.task_eager_propagates = True
        yield
    finally:
        config.task_always_eager = previous_eager
        config.task_eager_propagates = previous_propagates
