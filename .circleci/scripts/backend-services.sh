#!/usr/bin/env bash
set -euo pipefail
test "${CI:-}" = true
test "${CIRCLECI:-}" = true
test "${AURUM_DISPOSABLE_DB_CONFIRM:-}" = destroy-this-database
test "${POSTGRES_SERVICE_ID:-}" = aurum-ci-postgres
docker run --detach --name aurum-ci-postgres --publish 5432:5432 \
  --env POSTGRES_USER=postgres --env POSTGRES_PASSWORD=postgres --env POSTGRES_DB=aurum_test \
  postgres:16-alpine@sha256:4327b9fd295502f326f44153a1045a7170ddbfffed1c3829798328556cfd09e2
docker run --detach --name aurum-ci-redis --publish 6379:6379 \
  redis:7-alpine@sha256:02f2cc4882f8bf87c79a220ac958f58c700bdec0dfb9b9ea61b62fb0e8f1bfcf
for attempt in $(seq 1 60); do
  if docker exec aurum-ci-postgres pg_isready -U postgres -d aurum_test && docker exec aurum-ci-redis redis-cli ping; then
    exit 0
  fi
  sleep 2
done
echo 'Disposable database services did not become ready' >&2
exit 1
