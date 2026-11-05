#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
docker compose exec -T -e AURUM_E2E_SEED=1 backend python -m app.seed_e2e
docker compose exec -T backend python -m app.seed_demo
