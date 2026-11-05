$ErrorActionPreference = "Stop"
if ($env:CIRCLECI -ne "true") { throw "CircleCI runner required" }
./scripts/backend-isolated.ps1 -Check Tests tests/core/test_access_token_contract.py tests/domains/auth/test_token_contract.py -q --tb=short

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
