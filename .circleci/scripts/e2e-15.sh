#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
docker compose down -v --remove-orphans