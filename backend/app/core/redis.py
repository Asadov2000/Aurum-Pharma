"""Async Redis client shared by the app."""

from __future__ import annotations

from redis.asyncio import Redis, from_url

from app.core.config import get_settings

settings = get_settings()

# redis.asyncio.from_url lacks type hints in the published stubs.
redis_client: Redis = from_url(  # type: ignore[no-untyped-call]
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
)
