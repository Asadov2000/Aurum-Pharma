#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
poetry check --lock