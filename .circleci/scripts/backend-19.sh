#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
if docker container inspect aurum-ci-minio > /dev/null 2>&1; then
  docker rm --force aurum-ci-minio
fi
