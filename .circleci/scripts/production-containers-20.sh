#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
test "$(docker image inspect --format '{{.Config.User}}' aurum-backend:ci)" = "10001:10001"
test "$(docker image inspect --format '{{.Config.User}}' aurum-gateway:ci)" = "10001:10001"

backend_cmd="$(docker image inspect --format '{{json .Config.Cmd}}' aurum-backend:ci)"
python -c 'import json, sys; assert json.loads(sys.argv[1])[:3] == ["python", "-m", "uvicorn"]' "$backend_cmd"
if grep -q -- '--reload' <<<"$backend_cmd"; then
  echo "::error title=Unsafe production command::Backend image contains --reload"
  exit 1
fi
for module in uvicorn celery; do
  docker run --rm --network none --read-only --entrypoint python \
    aurum-backend:ci -m "$module" --version
done
