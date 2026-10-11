#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
export AURUM_RECOVERY_USE_PREBUILT=true
sh ./scripts/test-recovery-tooling.sh
