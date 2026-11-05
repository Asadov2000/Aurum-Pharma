#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
node website/scripts/test.mjs