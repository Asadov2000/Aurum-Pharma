#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
docker compose \
  --env-file .env.production.example \
  --file docker-compose.production.yml \
  --profile maintenance \
  config --quiet

docker compose \
  --env-file .env.production.example \
  --file docker-compose.production.yml \
  --file docker-compose.recovery.yml \
  --profile backup \
  --profile restore-drill \
  --profile offsite \
  --profile offsite-test \
  config --quiet

docker compose \
  --env-file .env.edge-shadow.example \
  --file docker-compose.edge-shadow.hardened.yml \
  config --format json > "$RUNNER_TEMP/edge-shadow-compose.json"

python - "$RUNNER_TEMP/edge-shadow-compose.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    edge = json.load(source)

assert not any(service.get("ports") for service in edge["services"].values())
assert edge["networks"]["edge-data"]["internal"] is True
worker = edge["services"]["edge-worker"]
assert worker["read_only"] is True
assert worker["cap_drop"] == ["ALL"]
assert worker["environment"]["EDGE_WRITER_ACTIVATION_ENABLED"] == "false"
assert worker["environment"]["EDGE_WRITER_READINESS_ENABLED"] == "false"
assert worker["environment"]["EDGE_SYNC_CLOUD_URL"].startswith("https://")
assert not worker.get("volumes"), "Edge worker must not mount source code"
assert edge["services"]["edge-migrate"]["build"]["dockerfile"] == (
    "Dockerfile.production"
)
PY

docker compose \
  --env-file .env.production.example \
  --file docker-compose.production.yml \
  --profile maintenance \
  config --format json > "$RUNNER_TEMP/production-compose.json"

docker compose \
  --env-file .env.production.example \
  --file docker-compose.production.yml \
  --file docker-compose.recovery.yml \
  --profile backup \
  --profile restore-drill \
  --profile offsite \
  --profile offsite-test \
  config --format json > "$RUNNER_TEMP/recovery-compose.json"

docker compose \
  --env-file .env.example \
  --file docker-compose.yml \
  --profile maintenance \
  config --format json > "$RUNNER_TEMP/development-compose.json"

python - \
  "$RUNNER_TEMP/production-compose.json" \
  "$RUNNER_TEMP/development-compose.json" \
  "$RUNNER_TEMP/recovery-compose.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    model = json.load(source)
with open(sys.argv[2], encoding="utf-8") as source:
    development_model = json.load(source)
with open(sys.argv[3], encoding="utf-8") as source:
    recovery_model = json.load(source)

published = sorted(
    name for name, service in model["services"].items() if service.get("ports")
)
assert published == ["gateway"], f"unexpected published services: {published}"

for name in ("app", "db", "broker", "storage", "monitoring"):
    assert model["networks"][name]["internal"] is True, (
        f"{name} network must be internal"
    )

hardened = (
    "backend", "celery-worker", "catalog-worker", "billing-worker",
    "celery-beat", "platform-mailer", "gateway"
)
for name in hardened:
    service = model["services"][name]
    assert service["read_only"] is True, (
        f"{name} root filesystem must be read-only"
    )
    assert service["cap_drop"] == ["ALL"], f"{name} must drop all capabilities"
    assert "no-new-privileges:true" in service["security_opt"], (
        f"{name} must prevent privilege escalation"
    )

backend_command = " ".join(model["services"]["backend"]["command"])
assert "--reload" not in backend_command, "backend Compose command enables reload"
assert model["services"]["backend"]["command"][:3] == ["python", "-m", "uvicorn"]
for name in ("celery-worker", "catalog-worker", "billing-worker",
             "platform-mailer", "celery-beat"):
    assert model["services"][name]["command"][:3] == ["python", "-m", "celery"], (
        f"{name} must use the relocated interpreter, not a stale console-script shebang"
    )

runtime_services = ("backend", "celery-worker", "catalog-worker", "celery-beat")
forbidden_runtime_environment = {
    "DATABASE_URL_MIGRATION",
    "POSTGRES_PASSWORD",
    "AURUM_APP_PASSWORD",
    "AURUM_SUPPORT_PASSWORD",
    "AURUM_MIGRATOR_PASSWORD",
    "AURUM_MAILER_PASSWORD",
    "AURUM_BILLING_WORKER_PASSWORD",
    "AURUM_WORKER_PASSWORD",
    "AURUM_CATALOG_WORKER_PASSWORD",
    "AURUM_BACKUP_PASSWORD",
    "AURUM_PITR_PASSWORD",
    "AURUM_OFFSITE_ACCESS_KEY",
    "AURUM_OFFSITE_SECRET_KEY",
    "TEST_DATABASE_URL_APP",
    "TEST_DATABASE_URL_SUPPORT",
    "TEST_DATABASE_URL_MIGRATION",
    "TEST_DATABASE_URL_MAILER",
    "TEST_DATABASE_URL_BILLING_WORKER",
    "TEST_DATABASE_URL_WORKER",
    "TEST_DATABASE_URL_CATALOG_WORKER",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "DATABASE_URL_BACKUP",
    "DATABASE_URL_PITR",
}
for compose_model in (model, development_model):
    for name in runtime_services:
        environment = set(
            compose_model["services"][name].get("environment", {})
        )
        leaked = environment & forbidden_runtime_environment
        assert not leaked, (
            f"{name} receives maintenance environment: {sorted(leaked)}"
        )
    billing_environment = set(
        compose_model["services"]["billing-worker"].get("environment", {})
    )
    forbidden_billing_environment = {
        "DATABASE_URL_APP",
        "DATABASE_URL_SUPPORT",
        "DATABASE_URL_MIGRATION",
        "DATABASE_URL_MAILER",
        "JWT_SECRET",
        "MFA_ENCRYPTION_KEY",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "EMAIL_PASSWORD",
    }
    leaked = billing_environment & forbidden_billing_environment
    assert not leaked, (
        "billing-worker receives unrelated credentials: "
        f"{sorted(leaked)}"
    )

forbidden_runtime_secrets = {
    "postgres_password",
    "aurum_migrator_password",
    "database_url_migration",
}
for name in runtime_services:
    mounted = {
        secret["source"] for secret in model["services"][name].get("secrets", [])
    }
    assert not mounted & forbidden_runtime_secrets, (
        f"{name} receives maintenance secrets: "
        f"{sorted(mounted & forbidden_runtime_secrets)}"
    )

expected_runtime_secrets = {
    "backend": {
        "database_url_app",
        "database_url_support",
        "redis_url",
        "celery_api_broker_url",
        "jwt_secret",
        "mfa_encryption_key",
        "mfa_previous_keys",
        "email_outbox_encryption_key",
        "metrics_token",
        "minio_access_key",
        "minio_secret_key",
    },
    "celery-worker": {"database_url_worker", "celery_system_broker_url"},
    "catalog-worker": {
        "database_url_catalog_worker",
        "celery_catalog_broker_url",
        "catalog_minio_access_key",
        "catalog_minio_secret_key",
    },
    "celery-beat": {"celery_beat_broker_url"},
}
for name, expected in expected_runtime_secrets.items():
    mounted = {
        secret["source"]
        for secret in model["services"][name].get("secrets", [])
    }
    assert mounted == expected, (
        f"unexpected {name} secrets: {sorted(mounted)}"
    )

credential_environment = {
    "CATALOG_MINIO_ACCESS_KEY",
    "CATALOG_MINIO_SECRET_KEY",
    "DATABASE_URL_APP",
    "DATABASE_URL_SUPPORT",
    "DATABASE_URL_WORKER",
    "DATABASE_URL_CATALOG_WORKER",
    "REDIS_URL",
    "CELERY_BROKER_URL",
    "JWT_SECRET",
    "MFA_ENCRYPTION_KEY",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "EMAIL_PASSWORD",
}
expected_development_credentials = {
    "celery-worker": {"DATABASE_URL_WORKER", "CELERY_BROKER_URL"},
    "catalog-worker": {
        "DATABASE_URL_CATALOG_WORKER",
        "CELERY_BROKER_URL",
        "CATALOG_MINIO_ACCESS_KEY",
        "CATALOG_MINIO_SECRET_KEY",
    },
    "celery-beat": {"CELERY_BROKER_URL"},
}
for name, expected in expected_development_credentials.items():
    present = (
        set(development_model["services"][name].get("environment", {}))
        & credential_environment
    )
    assert present == expected, (
        f"unexpected development {name} credentials: {sorted(present)}"
    )

catalog_command = " ".join(model["services"]["catalog-worker"]["command"])
assert "app.tasks.catalog_app" in catalog_command
assert "-Q catalog-worker" in catalog_command
assert set(model["services"]["catalog-worker"]["networks"]) == {"db", "broker", "storage"}
for compose_model in (model, development_model):
    worker = compose_model["services"]["catalog-worker"]
    assert worker["depends_on"]["catalog-minio-init"]["condition"] == (
        "service_completed_successfully"
    )
catalog_secret_targets = {
    entry["source"]: entry["target"]
    for entry in model["services"]["catalog-worker"]["secrets"]
}
assert catalog_secret_targets == {
    "database_url_catalog_worker": "DATABASE_URL_CATALOG_WORKER",
    "celery_catalog_broker_url": "CELERY_BROKER_URL",
    "catalog_minio_access_key": "CATALOG_MINIO_ACCESS_KEY",
    "catalog_minio_secret_key": "CATALOG_MINIO_SECRET_KEY",
}
assert set(model["services"]["celery-beat"]["networks"]) == {"broker"}

expected_resource_networks = {
    "postgres": {"db"}, "redis": {"broker"}, "minio": {"storage"},
    "minio-init": {"storage"}, "catalog-minio-init": {"storage"},
    "db-role-bootstrap": {"db"}, "migrate": {"db"},
    "celery-worker": {"db", "broker"},
    "catalog-worker": {"db", "broker", "storage"},
    "billing-worker": {"db", "broker"},
    "platform-mailer": {"db", "broker", "mailer-egress"},
    "celery-beat": {"broker"},
    "backend": {"app", "db", "broker", "storage", "monitoring"},
}
for service_name, expected_networks in expected_resource_networks.items():
    assert set(model["services"][service_name]["networks"]) == expected_networks, service_name
worker_roles = {
    "celery-worker": "system", "catalog-worker": "catalog",
    "billing-worker": "billing", "platform-mailer": "mailer",
}
for service_name, role in worker_roles.items():
    service = model["services"][service_name]
    command = service["command"]
    for flag in ("--without-gossip", "--without-mingle", "--without-heartbeat",
                 "--pidfile=/tmp/celery-worker.pid"):
        assert flag in command, (service_name, flag)
    assert service["healthcheck"]["test"] == [
        "CMD", "python", "-m", "app.tasks.worker_health", role, "/tmp/celery-worker.pid"
    ], service_name
assert "app.tasks.beat_app" in model["services"]["celery-beat"]["command"]

migrate_secrets = {
    secret["source"]
    for secret in model["services"]["migrate"].get("secrets", [])
}
assert migrate_secrets == {
    "database_url_support",
    "database_url_migration",
}, f"unexpected migrate secrets: {sorted(migrate_secrets)}"

bootstrap_secrets = {
    secret["source"]
    for secret in model["services"]["db-role-bootstrap"].get("secrets", [])
}
assert bootstrap_secrets == {
    "postgres_password",
    "aurum_app_password",
    "aurum_support_password",
    "aurum_migrator_password",
    "aurum_mailer_password",
    "aurum_billing_worker_password",
    "aurum_worker_password",
    "aurum_catalog_worker_password",
    "aurum_backup_password",
    "aurum_pitr_password",
}, f"unexpected bootstrap secrets: {sorted(bootstrap_secrets)}"

restore_postgres = recovery_model["services"]["restore-postgres"]
assert restore_postgres["environment"]["AURUM_CATALOG_WORKER_PASSWORD_FILE"] == (
    "/run/secrets/AURUM_CATALOG_WORKER_PASSWORD"
)
assert {
    "source": "aurum_catalog_worker_password",
    "target": "AURUM_CATALOG_WORKER_PASSWORD",
}.items() <= next(
    entry for entry in restore_postgres["secrets"]
    if entry["source"] == "aurum_catalog_worker_password"
).items()

postgres_command = " ".join(model["services"]["postgres"]["command"])
assert "ssl=on" in postgres_command
assert "ssl_min_protocol_version=TLSv1.2" in postgres_command
assert "archive_mode=on" in postgres_command
assert "archive_timeout=60s" in postgres_command
assert "archive-wal.sh" in postgres_command
postgres_targets = {
    volume["target"] for volume in model["services"]["postgres"]["volumes"]
}
assert "/wal-archive" in postgres_targets
assert "/run/tls/postgres.key" in postgres_targets

with open("infra/postgres/pg_hba.production.conf", encoding="utf-8") as source:
    pg_hba = source.read()
assert "hostssl all" in pg_hba
assert "\nhost    all" not in pg_hba

redis_service = model["services"]["redis"]
assert redis_service["entrypoint"] == ["/bin/sh", "/config/start-with-worker-acl.sh"]
assert redis_service["environment"]["REDIS_TLS"] == "true"
assert {entry["target"] for entry in redis_service["secrets"]} == {
    "REDIS_PASSWORD", *(
        f"REDIS_CELERY_{role}_PASSWORD"
        for role in ("API", "BEAT", "SYSTEM", "CATALOG", "BILLING", "MAILER")
    )
}
acl_mount = next(volume for volume in redis_service["volumes"]
                 if volume["target"] == "/config/start-with-worker-acl.sh")
assert acl_mount["read_only"] is True

minio_command = " ".join(model["services"]["minio"]["command"])
assert "--certs-dir /tmp/certs" in minio_command
assert model["services"]["backend"]["environment"]["MINIO_SECURE"] == "true"

tls_clients = (
    "backend", "celery-worker", "catalog-worker", "billing-worker",
    "platform-mailer", "celery-beat", "migrate", "db-role-bootstrap",
)
for name in tls_clients:
    targets = {
        volume["target"]
        for volume in model["services"][name].get("volumes", [])
    }
    assert "/run/tls/ca.crt" in targets, f"{name} does not trust internal CA"

assert recovery_model["networks"]["recovery"]["internal"] is True
assert not any(
    service.get("ports")
    for service in recovery_model["services"].values()
    if service.get("profiles")
)
offsite = recovery_model["services"]["offsite-sync"]
assert {
    secret["source"] for secret in offsite["secrets"]
} == {"aurum_offsite_access_key", "aurum_offsite_secret_key"}
assert set(offsite["networks"]) == {"backup-egress"}
assert all(
    volume.get("read_only") is True
    for volume in offsite["volumes"]
    if volume["target"] == "/repository"
)
pitr_secrets = {
    secret["source"]
    for secret in recovery_model["services"]["pitr-basebackup"]["secrets"]
}
assert pitr_secrets == {"database_url_pitr", "restic_password"}
for name in ("minio-backup-init", "backup", "pitr-basebackup"):
    targets = {
        volume["target"]
        for volume in recovery_model["services"][name].get("volumes", [])
    }
    assert "/run/tls/ca.crt" in targets, (
        f"{name} does not trust the production internal CA"
    )
wal_mount = next(
    volume
    for volume in recovery_model["services"]["wal-snapshot"]["volumes"]
    if volume["target"] == "/wal-archive"
)
assert wal_mount["read_only"] is True

assert model["services"]["redis"]["environment"]["AURUM_REDIS_MAXMEMORY"]
assert model["services"]["redis"]["logging"]["driver"] == "local"
recovery_exporter = model["services"]["aurum-recovery-exporter"]
assert set(recovery_exporter["networks"]) == {"monitoring"}
assert recovery_exporter["read_only"] is True
assert recovery_exporter["cap_drop"] == ["ALL"]
assert not recovery_exporter.get("ports")
metrics_mount = next(
    volume
    for volume in recovery_exporter["volumes"]
    if volume["target"] == "/metrics"
)
assert metrics_mount["read_only"] is True
for name, service in model["services"].items():
    assert service.get("mem_limit"), f"{name} has no memory limit"
    assert service.get("cpus"), f"{name} has no CPU limit"

billing_worker_secrets = {
    secret["source"]
    for secret in model["services"]["billing-worker"].get("secrets", [])
}
assert billing_worker_secrets == {
    "database_url_billing_worker",
    "celery_billing_broker_url",
}, f"unexpected billing worker secrets: {sorted(billing_worker_secrets)}"
assert set(model["services"]["billing-worker"]["networks"]) == {"db", "broker"}

mailer_secrets = {
    secret["source"]
    for secret in model["services"]["platform-mailer"].get("secrets", [])
}
assert mailer_secrets == {
    "database_url_mailer",
    "celery_mailer_broker_url",
    "email_outbox_encryption_key",
    "email_outbox_previous_keys",
    "email_password",
}, f"unexpected mailer secrets: {sorted(mailer_secrets)}"
assert set(model["services"]["platform-mailer"]["networks"]) == {
    "db", "broker",
    "mailer-egress",
}
PY
