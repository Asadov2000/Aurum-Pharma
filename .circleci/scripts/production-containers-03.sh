#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
docker build --check --file backend/Dockerfile.production backend
docker build --check --file frontend/Dockerfile.production frontend
docker build --check --file infra/backup/Dockerfile infra/backup
