#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
node --test scripts/audit-dependencies.test.mjs
node scripts/audit-dependencies.mjs
