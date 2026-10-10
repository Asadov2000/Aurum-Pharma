#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
pnpm exec playwright install --with-deps chromium