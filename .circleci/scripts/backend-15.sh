#!/usr/bin/env bash
set -euo pipefail
test "${CI:-}" = true
test "${CIRCLECI:-}" = true
test "${CIRCLE_NODE_TOTAL:-}" = 2
case "${CIRCLE_NODE_INDEX:-}" in
  0|1) ;;
  *) echo 'Expected CircleCI backend node index 0 or 1' >&2; exit 1 ;;
esac
mkdir -p test-results
poetry run pytest -p tests.sharding tests/ -q -x -o faulthandler_timeout=120 --durations=25 \
  --shard-index="$((CIRCLE_NODE_INDEX + 1))" --shard-count=2 \
  --shard-manifest=test-results/shard.json --junitxml=test-results/pytest.xml
