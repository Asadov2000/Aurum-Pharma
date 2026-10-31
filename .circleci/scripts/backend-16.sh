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

# These are fictional, redacted snapshots, not credentials or execution grants.
# The native SQL checks retain and test locks; the pure checker only verifies
# the captured evidence after that transaction has ended.
draft_fixtures="$(mktemp "$PWD/.edge-draft-check.XXXXXX")"
trap 'rm -f -- "$draft_fixtures"' EXIT
docker cp "$POSTGRES_SERVICE_ID:/tmp/aurum-edge-draft-fixtures.jsonl" "$draft_fixtures"
CI=true AURUM_DISPOSABLE_DB_CONFIRM=destroy-this-database \
  PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" \
  poetry run python ../scripts/check-edge-retail-draft-fixtures.py "$draft_fixtures"
