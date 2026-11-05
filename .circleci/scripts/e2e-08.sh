#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
docker compose up -d --build postgres redis minio
docker compose run --rm db-role-bootstrap
docker compose run --rm --build migrate
docker compose up -d --build backend celery-worker catalog-worker billing-worker platform-mailer celery-beat frontend
docker compose ps
echo "export E2E_POSTGRES_CONTAINER=$(docker compose ps --quiet postgres)" >> "$BASH_ENV"
echo "export E2E_REDIS_CONTAINER=$(docker compose ps --quiet redis)" >> "$BASH_ENV"
