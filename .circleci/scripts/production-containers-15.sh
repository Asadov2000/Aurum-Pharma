#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
for target in \
  /workspace/backend/Dockerfile.production \
  /workspace/frontend/Dockerfile.production \
  /workspace/infra/backup/Dockerfile; do
  docker run --rm \
    --volume "$PWD:/workspace:ro" \
    --volume "$RUNNER_TEMP/trivy-cache:/root/.cache/" \
    aquasec/trivy:0.74.0@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969 \
    config --severity HIGH,CRITICAL --exit-code 1 "$target"
done
