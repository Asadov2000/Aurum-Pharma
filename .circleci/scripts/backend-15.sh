#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
mkdir -p test-results
poetry run pytest -p tests.sharding tests/ -q --durations=25 \
  --shard-index=1 --shard-count=1 \
  --shard-manifest=test-results/shard.json --junitxml=test-results/pytest.xml
