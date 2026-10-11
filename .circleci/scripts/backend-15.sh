#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
mkdir -p test-results
# Check migration and privilege regressions before the full backend suite.
poetry run pytest -q -x -o faulthandler_timeout=120 --durations=5 \
  --junitxml=test-results/pytest-preflight.xml \
  tests/isolation/test_manual_access_migrations.py \
  tests/isolation/test_mixed_return_journal_migration.py \
  tests/isolation/test_runtime_role_privileges.py
poetry run pytest -p tests.sharding tests/ -q -x -o faulthandler_timeout=120 --durations=25 \
  --shard-index=1 --shard-count=1 \
  --shard-manifest=test-results/shard.json --junitxml=test-results/pytest.xml
