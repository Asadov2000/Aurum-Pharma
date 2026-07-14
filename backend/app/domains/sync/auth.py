"""Fail-closed machine authentication for the development shadow transport."""

from __future__ import annotations

import hashlib
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, Header, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import AppSessionLocal
from app.core.deps import get_redis
from app.core.errors import (
    AuthenticationError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
)
from app.domains.sync.credentials import parse_edge_credential


@dataclass(frozen=True, slots=True)
class EdgePrincipal:
    node_id: UUID
    tenant_id: UUID
    branch_id: UUID
    shadow_start_sequence: int
    shadow_start_checksum: str
    shadow_start_projection_checksum: str


@dataclass(slots=True)
class EdgeRequestContext:
    session: AsyncSession
    principal: EdgePrincipal


async def _rate_limit(redis: Redis, *, key: str, limit: int) -> None:
    try:
        pipeline = redis.pipeline(transaction=True)
        pipeline.incr(key)
        pipeline.expire(key, 120)
        results = cast(list[object], await pipeline.execute())
        count = int(cast(int, results[0]))
    except RedisError as exc:
        raise ServiceUnavailableError("Edge authentication guard is unavailable") from exc
    if count > limit:
        raise RateLimitError("Edge request rate exceeded")


async def _claim_nonce(redis: Redis, *, credential_hash: str, nonce: UUID, ttl: int) -> None:
    key = f"sync:edge:nonce:{credential_hash}:{nonce}"
    try:
        claimed = await redis.set(key, "1", ex=ttl, nx=True)
    except RedisError as exc:
        raise ServiceUnavailableError("Edge replay guard is unavailable") from exc
    if not claimed:
        raise AuthenticationError("Edge request was already used")


def _parse_request_auth(
    *, authorization: str | None, timestamp_header: str | None, nonce_header: str | None
) -> tuple[UUID, str, int, UUID]:
    if authorization is None or not authorization.lower().startswith("aurumedge "):
        raise AuthenticationError("Edge credential required")
    token = authorization.split(" ", 1)[1].strip()
    try:
        credential = parse_edge_credential(token)
        request_timestamp = int(timestamp_header or "")
        nonce = UUID(nonce_header or "")
    except (TypeError, ValueError) as exc:
        raise AuthenticationError("Invalid Edge authentication headers") from exc
    if nonce.version != 4 or str(nonce) != (nonce_header or "").lower():
        raise AuthenticationError("Edge nonce must be a canonical UUIDv4")
    return credential.kid, credential.digest, request_timestamp, nonce


async def get_edge_context(
    request: Request,
    redis: Annotated[Redis, Depends(get_redis)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    request_timestamp_header: Annotated[str | None, Header(alias="X-Aurum-Timestamp")] = None,
    nonce_header: Annotated[str | None, Header(alias="X-Aurum-Nonce")] = None,
) -> AsyncIterator[EdgeRequestContext]:
    settings = get_settings()
    if not settings.EDGE_SYNC_ENABLED:
        raise NotFoundError("Edge sync API is disabled")
    kid, credential_hash, request_timestamp, nonce = _parse_request_auth(
        authorization=authorization,
        timestamp_header=request_timestamp_header,
        nonce_header=nonce_header,
    )
    now = int(time.time())
    if abs(now - request_timestamp) > settings.EDGE_SYNC_MAX_CLOCK_SKEW_SECONDS:
        raise AuthenticationError("Edge request timestamp is outside the allowed window")

    peer = request.client.host if request.client is not None else "unknown"
    peer_key = hashlib.sha256(peer.encode("utf-8")).hexdigest()[:24]
    # The client timestamp is freshness input, not a rate-limit clock. Using the
    # server clock prevents a client from spreading requests across valid skew
    # buckets to multiply its allowance.
    bucket = now // 60
    await _rate_limit(
        redis,
        key=f"sync:edge:rate:peer:{peer_key}:{bucket}",
        limit=settings.EDGE_SYNC_REQUESTS_PER_MINUTE,
    )

    async with AppSessionLocal() as session:
        async with session.begin():
            result = await session.execute(
                text("SELECT * FROM public.authenticate_edge_node(:kid, :credential_hash)"),
                {"kid": kid, "credential_hash": credential_hash},
            )
            row = result.mappings().one_or_none()
            if row is None:
                raise AuthenticationError("Invalid or expired Edge credential")
            principal = EdgePrincipal(
                node_id=row["node_id"],
                tenant_id=row["tenant_id"],
                branch_id=row["branch_id"],
                shadow_start_sequence=int(row["shadow_start_sequence"]),
                shadow_start_checksum=str(row["shadow_start_checksum"]),
                shadow_start_projection_checksum=str(row["shadow_start_projection_checksum"]),
            )
            await _rate_limit(
                redis,
                key=f"sync:edge:rate:node:{principal.node_id}:{bucket}",
                limit=settings.EDGE_SYNC_REQUESTS_PER_MINUTE,
            )
            await _claim_nonce(
                redis,
                credential_hash=credential_hash,
                nonce=nonce,
                ttl=settings.EDGE_SYNC_NONCE_TTL_SECONDS,
            )
            await session.execute(
                text("SELECT set_config('app.tenant_id', :value, true)"),
                {"value": str(principal.tenant_id)},
            )
            await session.execute(
                text("SELECT set_config('app.branch_id', :value, true)"),
                {"value": str(principal.branch_id)},
            )
            await session.execute(
                text("SELECT set_config('app.edge_node_id', :value, true)"),
                {"value": str(principal.node_id)},
            )
            yield EdgeRequestContext(session=session, principal=principal)
