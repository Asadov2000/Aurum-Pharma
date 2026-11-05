#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
poetry check --lock
poetry install --no-root --with dev --sync
# pip is build tooling, not a Poetry project dependency. Pin the
# audited environment to the current patched release for PYSEC-2026-3721.
poetry run python -m pip install --disable-pip-version-check --upgrade pip==26.2.1
