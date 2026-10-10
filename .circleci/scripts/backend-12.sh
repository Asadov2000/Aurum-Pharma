#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
poetry run ruff check app tests