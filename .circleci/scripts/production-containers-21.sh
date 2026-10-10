#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
docker run --rm --env AURUM_DOMAIN=pharmacy.example.com --env AURUM_ACME_EMAIL=ops@example.com --entrypoint caddy aurum-gateway:ci validate --config /etc/caddy/Caddyfile --adapter caddyfile
