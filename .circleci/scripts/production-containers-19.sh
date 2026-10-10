#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
mkdir -p "$RUNNER_TEMP/prometheus-secrets"
printf '%064d' 0 > "$RUNNER_TEMP/prometheus-secrets/METRICS_TOKEN"
docker run --rm \
  --entrypoint /bin/promtool \
  --volume "$PWD/infra/prometheus:/etc/prometheus:ro" \
  --volume "$RUNNER_TEMP/prometheus-secrets:/run/secrets:ro" \
  prom/prometheus:v3.13.1@sha256:3c42b892cf723fa54d2f262c37a0e1f80aa8c8ddb1da7b9b0df9455a35a7f893 \
  check config /etc/prometheus/prometheus.production.yml
