#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
docker run --detach \
  --name aurum-ci-minio \
  --publish 9000:9000 \
  --env MINIO_ROOT_USER=minioadmin \
  --env MINIO_ROOT_PASSWORD=minioadmin \
  docker.io/pgsty/silo:RELEASE.2026-09-16T00-00-00Z@sha256:635197cb9f36d01bee221d34d1c7d7960f6a95c48b0b6c01d99cd13bdae51a46 \
  server /data

for attempt in $(seq 1 30); do
  if curl --fail --silent http://localhost:9000/minio/health/live > /dev/null; then
    echo "MinIO is ready"
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    docker logs aurum-ci-minio
    exit 1
  fi
  sleep 2
done
