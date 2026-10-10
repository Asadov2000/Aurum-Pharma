#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
docker compose logs --no-color --tail=250 postgres redis minio backend celery-worker catalog-worker billing-worker platform-mailer celery-beat frontend
