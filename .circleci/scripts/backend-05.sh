#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
poetry install --no-root --with dev --sync