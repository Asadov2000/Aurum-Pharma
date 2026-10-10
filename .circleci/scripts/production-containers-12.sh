#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
sh ./scripts/test-recovery-tooling.sh