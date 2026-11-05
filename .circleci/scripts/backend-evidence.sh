#!/usr/bin/env bash
set -euo pipefail
test "${CI:-}" = true
test "${CIRCLECI:-}" = true
test "${CIRCLE_NODE_TOTAL:-}" = 2
case "${CIRCLE_NODE_INDEX:-}" in
  0|1) ;;
  *) echo 'Expected CircleCI backend node index 0 or 1' >&2; exit 1 ;;
esac
if [[ ! "${CIRCLE_BUILD_NUM:-}" =~ ^[1-9][0-9]*$ ]]; then
  echo 'Expected a positive CircleCI build number without leading zeros' >&2
  exit 1
fi
test -s backend/test-results/pytest.xml
test -s backend/test-results/shard.json
# Reruns inherit workspace layers, so isolate each job attempt as well as node.
destination="ci-evidence/backend/run-${CIRCLE_BUILD_NUM}/shard-${CIRCLE_NODE_INDEX}"
mkdir -p "$destination"
cp backend/test-results/pytest.xml backend/test-results/shard.json "$destination/"
