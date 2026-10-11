#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
mkdir -p .circleci-tmp
case "${1:-}" in
  python)
    python -c 'import platform; print(platform.python_implementation()); print(platform.python_version())' > .circleci-tmp/python-runtime
    ;;
  pnpm)
    { node --version; pnpm --version; } > .circleci-tmp/pnpm-runtime
    ;;
  *)
    echo 'Expected download cache runtime: python or pnpm' >&2
    exit 1
    ;;
esac
