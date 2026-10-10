#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
pnpm install --frozen-lockfile