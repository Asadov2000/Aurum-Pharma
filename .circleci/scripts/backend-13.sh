#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
poetry run black --check app tests