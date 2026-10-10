#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
test -n "$POSTGRES_SERVICE_ID"
docker cp \
  ../scripts/test-edge-cash-dispatcher.sh \
  "$POSTGRES_SERVICE_ID:/tmp/test-edge-cash-dispatcher.sh"
docker exec -i \
  -e POSTGRES_HOST=127.0.0.1 \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_DB=aurum_test \
  -e POSTGRES_PASSWORD_FILE=/tmp/aurum-postgres-password \
  -e CI=true \
  -e CIRCLECI=true \
  -e AURUM_DISPOSABLE_DB_CONFIRM=destroy-this-database \
  "$POSTGRES_SERVICE_ID" \
  sh /tmp/test-edge-cash-dispatcher.sh
