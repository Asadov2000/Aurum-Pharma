"""Async Redis client shared by the app."""
from __future__ import annotations

from redis.asyncio import Redis, from_url

from app.core.config import get_settings

settings = get_settings()

redis_client: Redis = from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
)
