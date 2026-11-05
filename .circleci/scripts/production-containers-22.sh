#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
container=aurum-gateway-runtime-check
trap 'docker rm --force --volumes "$container" >/dev/null 2>&1 || true' EXIT

docker run --detach \
  --name "$container" \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=32m \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --env AURUM_DOMAIN=localhost \
  --env AURUM_ACME_EMAIL=ops@example.com \
  --volume /data \
  --volume /config \
  aurum-gateway:ci

for attempt in {1..15}; do
  if docker exec "$container" \
    wget --quiet --spider http://127.0.0.1:9080/healthz; then
    exit 0
  fi
  sleep 1
done

docker logs "$container"
exit 1
