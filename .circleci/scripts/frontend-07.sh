#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
mkdir -p test-results
pnpm exec vitest run tests \
  --reporter=dot \
  --reporter=junit \
  --outputFile.junit=test-results/vitest.xml
