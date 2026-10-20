#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
scan_status=0
for image in aurum-backend:ci aurum-gateway:ci aurum-backup:ci; do
  docker run --rm \
    --volume /var/run/docker.sock:/var/run/docker.sock \
    --volume "$RUNNER_TEMP/trivy-cache:/root/.cache/" \
    aquasec/trivy:0.74.0@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969 \
    image --ignore-unfixed --severity HIGH,CRITICAL --exit-code 1 "$image" || scan_status=1
done

exit "$scan_status"
