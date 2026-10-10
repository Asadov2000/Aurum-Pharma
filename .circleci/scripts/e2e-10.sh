#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
docker compose restart frontend

wait_for_url() {
  local name="$1"
  local url="$2"
  for attempt in $(seq 1 60); do
    if curl --fail --silent --show-error "$url" > /dev/null; then
      echo "$name is ready"
      return 0
    fi
    if [ "$attempt" -eq 60 ]; then
      echo "$name did not become ready: $url"
      return 1
    fi
    sleep 2
  done
}

wait_for_url "Backend" "http://localhost:18000/healthz"
wait_for_url "Frontend" "http://localhost:15173"
