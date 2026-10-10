#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
test -n "$POSTGRES_SERVICE_ID"
docker cp \
  ../infra/postgres/manage-edge-cash-identity.sql \
  "$POSTGRES_SERVICE_ID:/tmp/manage-edge-cash-identity.sql"
docker cp \
  ../infra/postgres/manage-edge-cash-identity.sh \
  "$POSTGRES_SERVICE_ID:/tmp/manage-edge-cash-identity.sh"
docker cp \
  ../scripts/test-edge-cash-identity.sh \
  "$POSTGRES_SERVICE_ID:/tmp/test-edge-cash-identity.sh"
docker exec -i \
  -e POSTGRES_HOST=127.0.0.1 \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_DB=aurum_test \
  -e POSTGRES_PASSWORD_FILE=/tmp/aurum-postgres-password \
  -e AURUM_APP_PASSWORD_FILE=/tmp/aurum-app-password \
  -e AURUM_SUPPORT_PASSWORD_FILE=/tmp/aurum-support-password \
  -e AURUM_MIGRATOR_PASSWORD_FILE=/tmp/aurum-migrator-password \
  -e AURUM_MAILER_PASSWORD_FILE=/tmp/aurum-mailer-password \
  -e AURUM_BILLING_WORKER_PASSWORD_FILE=/tmp/aurum-billing-worker-password \
  -e AURUM_WORKER_PASSWORD_FILE=/tmp/aurum-worker-password \
  -e AURUM_CATALOG_WORKER_PASSWORD_FILE=/tmp/aurum-catalog-worker-password \
  -e AURUM_BACKUP_PASSWORD_FILE=/tmp/aurum-backup-password \
  -e AURUM_PITR_PASSWORD_FILE=/tmp/aurum-pitr-password \
  -e EDGE_IDENTITY_SCRIPT=/tmp/manage-edge-cash-identity.sh \
  -e ROLE_BOOTSTRAP_SCRIPT=/tmp/bootstrap-roles.sh \
  -e CI=true \
  -e CIRCLECI=true \
  -e AURUM_DISPOSABLE_DB_CONFIRM=destroy-this-database \
  "$POSTGRES_SERVICE_ID" \
  sh /tmp/test-edge-cash-identity.sh
