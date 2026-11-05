#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
test -n "$POSTGRES_SERVICE_ID"
docker cp \
  ../infra/postgres/role-contract.sql \
  "$POSTGRES_SERVICE_ID:/tmp/role-contract.sql"
docker cp \
  ../infra/postgres/bootstrap-roles.sh \
  "$POSTGRES_SERVICE_ID:/tmp/bootstrap-roles.sh"
docker exec "$POSTGRES_SERVICE_ID" sh -c '
  printf %s postgres > /tmp/aurum-postgres-password
  printf %s aurum_app_pw > /tmp/aurum-app-password
  printf %s aurum_support_pw > /tmp/aurum-support-password
  printf %s aurum_migrator_pw > /tmp/aurum-migrator-password
  printf %s aurum_mailer_pw > /tmp/aurum-mailer-password
  printf %s aurum_billing_worker_pw > /tmp/aurum-billing-worker-password
  printf %s aurum_worker_pw > /tmp/aurum-worker-password
  printf %s aurum_catalog_worker_pw > /tmp/aurum-catalog-worker-password
  printf %s aurum_backup_pw > /tmp/aurum-backup-password
  printf %s aurum_pitr_pw > /tmp/aurum-pitr-password
'
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
  "$POSTGRES_SERVICE_ID" sh -c '
    unset \
      POSTGRES_PASSWORD \
      AURUM_APP_PASSWORD \
      AURUM_SUPPORT_PASSWORD \
      AURUM_MIGRATOR_PASSWORD \
      AURUM_MAILER_PASSWORD \
      AURUM_BILLING_WORKER_PASSWORD \
      AURUM_WORKER_PASSWORD \
      AURUM_CATALOG_WORKER_PASSWORD \
      AURUM_BACKUP_PASSWORD \
      AURUM_PITR_PASSWORD
    exec sh /tmp/bootstrap-roles.sh
  '
