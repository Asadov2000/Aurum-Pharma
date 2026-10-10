#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
mkdir -p "$RUNNER_TEMP/sbom"
for component in backend gateway backup; do
  docker run --rm \
    --volume /var/run/docker.sock:/var/run/docker.sock \
    --volume "$RUNNER_TEMP/sbom:/reports" \
    --volume "$RUNNER_TEMP/trivy-cache:/root/.cache/" \
    aquasec/trivy:0.74.0@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969 \
    image --format cyclonedx --output "/reports/$component.cdx.json" \
    "aurum-$component:ci"
  test -s "$RUNNER_TEMP/sbom/$component.cdx.json"
done
