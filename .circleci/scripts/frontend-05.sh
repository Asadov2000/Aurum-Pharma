#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
node ../scripts/build-brand-assets.mjs --check