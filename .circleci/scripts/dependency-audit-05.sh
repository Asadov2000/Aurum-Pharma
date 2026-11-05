#!/usr/bin/env bash
set -euo pipefail
test "${CIRCLECI:-}" = true
report=.pip-audit.json
trap 'rm -f -- "$report"' EXIT

for delay in 0 5 15; do
  sleep "$delay"
  : > "$report"

  poetry run pip-audit \
    --local \
    --format=json \
    --output="$report" \
    --progress-spinner=off \
    --desc=off \
    --aliases=on \
    --timeout=20 || true

  if [ -s "$report" ]; then
    python - "$report" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    dependencies = json.load(stream)["dependencies"]

skipped = [item for item in dependencies if item.get("skip_reason")]
vulnerabilities = [
    (item["name"], vulnerability["id"], vulnerability.get("fix_versions", []))
    for item in dependencies
    for vulnerability in item.get("vulns", [])
]

for item in skipped:
    print(f"SKIPPED {item['name']}: {item['skip_reason']}")
for name, vulnerability_id, fixes in vulnerabilities:
    print(f"VULNERABLE {name}: {vulnerability_id}; fixes={fixes}")

raise SystemExit(1 if skipped or vulnerabilities else 0)
PY
    exit 0
  fi
done

echo "::error title=Python audit unavailable::No complete report after 3 attempts"
exit 1
