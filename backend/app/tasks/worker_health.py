"""Local worker liveness and authenticated broker connectivity, without pidbox."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import cast

import redis

from app.core.celery_broker_config import BrokerRole, get_celery_broker_settings


def check_worker_health(role: BrokerRole, pidfile: Path) -> None:
    pid = int(pidfile.read_text(encoding="utf-8").strip())
    if pid <= 0:
        raise ValueError("Invalid worker PID")
    os.kill(pid, 0)
    settings = get_celery_broker_settings(role)
    with redis.Redis.from_url(
        settings.CELERY_BROKER_URL, socket_connect_timeout=3, socket_timeout=3
    ) as client:
        if not client.ping():
            raise RuntimeError("Broker health check failed")


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in {"system", "catalog", "billing", "mailer"}:
        return 1
    try:
        check_worker_health(cast(BrokerRole, sys.argv[1]), Path(sys.argv[2]))
    except (OSError, ValueError, redis.RedisError, RuntimeError):
        # Configuration and connection exceptions can contain credential-bearing URLs.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
