"""Actual Redis ACL, Celery transport and Docker network probes; synthetic data only."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import socketserver
import subprocess
import sys
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from celery import Celery
from redis import Redis
from redis.exceptions import AuthenticationError, NoPermissionError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tasks.broker_contract import configure_task_app

QUEUES = {
    "system": "default",
    "catalog": "catalog-worker",
    "billing": "billing-worker",
    "mailer": "platform-mailer",
}
PRODUCERS = {
    "api": ("catalog-worker",),
    "beat": ("default", "billing-worker", "platform-mailer"),
}


class TaskRequest(Protocol):
    id: str
    retries: int


class SyntheticTask(Protocol):
    request: TaskRequest

    def retry(self, *, countdown: int, max_retries: int) -> Exception: ...


def report(check: str, **values: object) -> None:
    print(json.dumps({"check": check, **values}), flush=True)


def broker_url(role: str) -> str:
    password = os.environ[f"REDIS_CELERY_{role.upper()}_PASSWORD"]
    return (
        f"redis://aurum_{role}:{quote(password, safe='')}@{os.environ['BROKER_TEST_HOST']}:6379/0"
    )


def redis_client(role: str | None = None) -> Redis:
    return Redis(
        host=os.environ["BROKER_TEST_HOST"],
        username=f"aurum_{role}" if role else None,
        password=(
            os.environ[f"REDIS_CELERY_{role.upper()}_PASSWORD"]
            if role
            else os.environ["REDIS_PASSWORD"]
        ),
        socket_timeout=2,
        socket_connect_timeout=2,
        decode_responses=True,
    )


def expect_denied(label: str, operation: Callable[[], object]) -> None:
    try:
        operation()
    except NoPermissionError:
        return
    raise AssertionError(f"Unexpected permission: {label}")


def reject_foreign_queue(client: Redis, role: str, foreign: str) -> int:
    forbidden_queue = [
        (
            "foreign_publish",
            lambda foreign=foreign: client.lpush(foreign, "unauthorized"),
        ),
        (
            "foreign_consume",
            lambda foreign=foreign: client.brpop(foreign, timeout=1),
        ),
        (
            "foreign_unacked_read",
            lambda foreign=foreign: client.hget(f"aurum:broker:{foreign}:unacked", "synthetic-tag"),
        ),
        (
            "foreign_unacked_write",
            lambda foreign=foreign: client.hset(f"aurum:broker:{foreign}:unacked", "tag", "bad"),
        ),
        (
            "foreign_unacked_index",
            lambda foreign=foreign: client.zadd(
                f"aurum:broker:{foreign}:unacked_index", {"bad": 2}
            ),
        ),
        (
            "foreign_unacked_mutex",
            lambda foreign=foreign: client.set(f"aurum:broker:{foreign}:unacked_mutex", "bad"),
        ),
        (
            "foreign_binding",
            lambda foreign=foreign: client.sadd(f"_kombu.binding.{foreign}", "bad"),
        ),
        (
            "foreign_binding_read",
            lambda foreign=foreign: client.smembers(f"_kombu.binding.{foreign}"),
        ),
    ]
    for priority in (3, 6, 9):
        forbidden_queue.append(
            (
                f"foreign_priority_{priority}",
                partial(client.lpush, f"{foreign}\x06\x16{priority}", "bad"),
            )
        )
    for label, operation in forbidden_queue:
        expect_denied(f"{role}:{foreign}:{label}", operation)
    return len(forbidden_queue)


def reject_producer_consume_tracking(client: Redis, role: str, queue: str) -> int:
    expect_denied(f"{role}:{queue}:consume", partial(client.rpop, queue))
    expect_denied(
        f"{role}:{queue}:blocking_consume",
        partial(client.brpop, [queue], timeout=1),
    )
    forbidden_tracking = (
        partial(client.hget, f"aurum:broker:{queue}:unacked", "synthetic-tag"),
        partial(client.hset, f"aurum:broker:{queue}:unacked", "bad", "bad"),
        partial(client.zadd, f"aurum:broker:{queue}:unacked_index", {"bad": 2}),
        partial(client.set, f"aurum:broker:{queue}:unacked_mutex", "bad"),
    )
    for operation in forbidden_tracking:
        expect_denied(f"{role}:{queue}:tracking", operation)
    return 2 + len(forbidden_tracking)


def raw_acl() -> None:
    with redis_client() as root:
        root.set("auth:mfa-attempts:synthetic", "7")
        with Redis(host=os.environ["BROKER_TEST_HOST"], socket_timeout=2) as anonymous:
            anonymous_operations = (
                anonymous.ping,
                partial(anonymous.get, "auth:mfa-attempts:synthetic"),
                partial(anonymous.lpush, "catalog-worker", "unauthorized"),
                partial(anonymous.execute_command, "AUTH", "default", ""),
            )
            for operation in anonymous_operations:
                try:
                    operation()
                except AuthenticationError:
                    continue
                raise AssertionError("Anonymous or empty-password Redis access allowed")
        report("anonymous_acl", denied=len(anonymous_operations))
        for queue in QUEUES.values():
            root.rpush(queue, "synthetic-preserved-message")
            root.sadd(f"_kombu.binding.{queue}", "synthetic-preserved-binding")
            for priority in (3, 6, 9):
                root.rpush(f"{queue}\x06\x16{priority}", "synthetic-preserved-priority")
            root.hset(f"aurum:broker:{queue}:unacked", "synthetic-tag", "preserved-delivery")
            root.zadd(f"aurum:broker:{queue}:unacked_index", {"synthetic-tag": 1})
            root.set(f"aurum:broker:{queue}:unacked_mutex", "synthetic-lock")
        for role in (*PRODUCERS, *QUEUES):
            own = QUEUES.get(role) or PRODUCERS[role][0]
            allowed = set(PRODUCERS[role]) if role in PRODUCERS else {own}
            foreign_queues = set(QUEUES.values()) - allowed
            with redis_client(role) as client:
                assert client.ping(), "Own broker PING must work"
                client.lpush(own, "synthetic-new-message")
                assert root.lpop(own) == "synthetic-new-message"
                assert client.llen(own) == 1
                for priority in (3, 6, 9):
                    key = f"{own}\x06\x16{priority}"
                    client.lpush(key, "synthetic-priority-message")
                    assert root.lpop(key) == "synthetic-priority-message"
                denied = 0
                for foreign in sorted(foreign_queues):
                    denied += reject_foreign_queue(client, role, foreign)
                forbidden = [
                    ("cache_read", lambda: client.get("auth:mfa-attempts:synthetic")),
                    ("cache_write", lambda: client.set("auth:mfa-attempts:synthetic", "bad")),
                    ("cache_scan", lambda: client.scan()),
                    ("key_enumeration", lambda: client.keys("*")),
                    ("admin", lambda: client.config_get("maxmemory")),
                    ("acl_admin", lambda: client.acl_list()),
                    ("flush", client.flushdb),
                    ("publish", lambda: client.publish("foreign-channel", "bad")),
                    ("subscribe", lambda: client.execute_command("SUBSCRIBE", "foreign-channel")),
                ]
                for label, operation in forbidden:
                    expect_denied(f"{role}:{label}", operation)
                    denied += 1
                if role in PRODUCERS:
                    for queue in sorted(allowed):
                        denied += reject_producer_consume_tracking(client, role, queue)
            report("raw_acl", role=role, denied=denied)
        assert root.get("auth:mfa-attempts:synthetic") == "7"
        for queue in QUEUES.values():
            assert root.lrange(queue, 0, -1) == ["synthetic-preserved-message"]
            assert root.smembers(f"_kombu.binding.{queue}") == {"synthetic-preserved-binding"}
            for priority in (3, 6, 9):
                key = f"{queue}\x06\x16{priority}"
                assert root.lrange(key, 0, -1) == ["synthetic-preserved-priority"]
                root.delete(key)
            assert root.hgetall(f"aurum:broker:{queue}:unacked") == {
                "synthetic-tag": "preserved-delivery"
            }
            assert root.zrange(f"aurum:broker:{queue}:unacked_index", 0, -1) == ["synthetic-tag"]
            assert root.get(f"aurum:broker:{queue}:unacked_mutex") == "synthetic-lock"
            # Remove only our verified fixtures before genuine Celery traffic.
            root.delete(
                queue,
                f"_kombu.binding.{queue}",
                f"aurum:broker:{queue}:unacked",
                f"aurum:broker:{queue}:unacked_index",
                f"aurum:broker:{queue}:unacked_mutex",
            )
        report("raw_acl_integrity", result="passed")


def synthetic_app(role: str, url: str) -> Celery:
    app = Celery("boundary-test", broker=url)
    configure_task_app(app, queues=(QUEUES[role],), worker_queue=QUEUES[role])
    options = dict(app.conf.broker_transport_options)
    options.update(visibility_timeout=2, polling_interval=0.1)
    app.conf.broker_transport_options = options
    app.conf.visibility_timeout = 2
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.task_acks_late is True
    assert app.conf.worker_enable_remote_control is False
    assert app.conf.worker_send_task_events is False
    assert app.conf.task_ignore_result is True
    return app


def run_worker(role: str, marker_dir: Path) -> None:
    app = synthetic_app(role, os.environ["CELERY_BROKER_URL"])

    @app.task(bind=True, name="boundary.synthetic")  # type: ignore[misc]
    def synthetic(task: SyntheticTask, operation: str) -> None:
        task_id = str(task.request.id)
        marker = marker_dir / task_id
        if operation == "retry" and task.request.retries == 0:
            marker.with_suffix(".retry").write_text("retry", encoding="ascii")
            raise task.retry(countdown=1, max_retries=1)
        if operation == "crash" and not marker.with_suffix(".claimed").exists():
            marker.with_suffix(".claimed").write_text("claimed", encoding="ascii")
            time.sleep(60)
        marker.with_suffix(".done").write_text("done", encoding="ascii")

    app.worker_main(
        [
            "worker",
            "--pool=prefork",
            "--concurrency=1",
            f"--queues={QUEUES[role]}",
            "--without-gossip",
            "--without-mingle",
            "--without-heartbeat",
            "--loglevel=ERROR",
            f"--hostname=boundary-{role}@%h",
            f"--pidfile={marker_dir / 'worker.pid'}",
        ]
    )


def wait_until(check: Callable[[], bool], label: str, timeout: float = 35) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.1)
    raise AssertionError(f"Timed out: {label}")


def stop_worker(process: subprocess.Popen[bytes], *, crash: bool = False) -> None:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGKILL if crash else signal.SIGTERM)
    try:
        process.wait(timeout=6)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def start_worker(
    role: str, marker_dir: Path, worker_env: dict[str, str]
) -> subprocess.Popen[bytes]:
    (marker_dir / "worker.pid").unlink(missing_ok=True)
    with (marker_dir / "worker.log").open("ab") as output:
        return subprocess.Popen(
            [sys.executable, __file__, "worker", "--role", role, "--markers", str(marker_dir)],
            env=worker_env,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def unacked_empty(root: Redis, queue: str) -> bool:
    return root.hlen(f"aurum:broker:{queue}:unacked") == 0


def celery_delivery() -> None:
    for role, queue in QUEUES.items():
        marker_dir = Path("/tmp") / f"boundary-{role}"
        marker_dir.mkdir()
        worker_env = {
            "PATH": os.environ["PATH"],
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "CELERY_BROKER_URL": broker_url(role),
            "ENVIRONMENT": "development",
        }

        publisher_role = "api" if role == "catalog" else "beat"
        publisher = Celery("boundary-publisher", broker=broker_url(publisher_role))
        configure_task_app(publisher, queues=PRODUCERS[publisher_role])
        process = start_worker(role, marker_dir, worker_env)
        try:
            for operation in ("plain", "retry", "crash"):
                result = publisher.send_task(
                    "boundary.synthetic", args=[operation], queue=queue, retry=False
                )
                marker = marker_dir / result.id
                if operation == "crash":
                    wait_until(marker.with_suffix(".claimed").exists, f"{role}:claim")
                    stop_worker(process, crash=True)
                    with redis_client() as root:
                        assert root.hlen(f"aurum:broker:{queue}:unacked") == 1
                    # Kombu performs its first visibility scan at startup, then
                    # much less often. Let the real two-second lease expire first.
                    time.sleep(2.2)
                    process = start_worker(role, marker_dir, worker_env)
                wait_until(marker.with_suffix(".done").exists, f"{role}:{operation}")
                assert process.poll() is None, "Worker must remain alive"
                if operation == "retry":
                    assert marker.with_suffix(".retry").exists()
            health = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "app.tasks.worker_health",
                    role,
                    str(marker_dir / "worker.pid"),
                ],
                env=worker_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )
            assert health.returncode == 0, f"{role}:local worker health must pass"
            with redis_client() as root:
                wait_until(
                    partial(unacked_empty, root, queue),
                    f"{role}:acknowledged",
                )
            report(
                "celery_delivery", role=role, plain=True, retry=True, redelivery=True, health=True
            )
        finally:
            stop_worker(process)
            publisher.close()
            permission_errors = []
            for line in (marker_dir / "worker.log").read_text(errors="replace").splitlines():
                if "NoPermissionError" in line:
                    report("worker_permission_error", role=role, detail=line[:200])
                    permission_errors.append(line)
            assert not permission_errors, f"{role}:expected Celery transport command denied"


def network_probe(role: str) -> None:
    allowed = {
        "api": {"db", "broker", "storage"},
        "beat": {"broker"},
        "system": {"db", "broker"},
        "catalog": {"db", "broker", "storage"},
        "billing": {"db", "broker"},
        "mailer": {"db", "broker"},
    }[role]
    targets = json.loads(os.environ["BOUNDARY_NETWORK_TARGETS"])
    for name, target in targets.items():
        hostname, address, port = target
        for kind, host in (("dns", hostname), ("ip", address)):
            connected = False
            try:
                with socket.create_connection((host, int(port)), timeout=0.5):
                    connected = True
            except OSError:
                pass
            assert connected == (name in allowed), f"{role}:{name}:{kind} unexpected reachability"
            if kind == "dns" and name not in allowed:
                try:
                    socket.getaddrinfo(hostname, int(port))
                except socket.gaierror:
                    pass
                else:
                    raise AssertionError(f"{role}:{name} forbidden DNS name resolves")
    report("network", role=role, allowed=len(allowed), denied=3 - len(allowed), dns_and_ip=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("acl", "celery", "worker", "network", "listener"))
    parser.add_argument("--role", choices=(*PRODUCERS, *QUEUES), default="beat")
    parser.add_argument("--markers", type=Path, default=Path("/tmp/unused"))
    parser.add_argument("--port", type=int, default=5432)
    args = parser.parse_args()
    if args.mode == "worker":
        run_worker(args.role, args.markers)
    elif args.mode == "listener":
        with socketserver.ThreadingTCPServer(
            ("0.0.0.0", args.port), socketserver.BaseRequestHandler
        ) as server:
            server.serve_forever()
    elif args.mode == "network":
        network_probe(args.role)
    else:
        if not os.environ["BROKER_TEST_HOST"].startswith("aurum-broker-test-"):
            raise ValueError("Use the isolated worker broker test launcher")
        if args.mode == "acl":
            raw_acl()
        else:
            celery_delivery()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Connection errors may include endpoint details. Only fixed assertions are printed.
        report(
            "failure",
            error_type=type(error).__name__,
            detail=str(error) if isinstance(error, AssertionError) else "",
        )
        raise SystemExit(1) from None
