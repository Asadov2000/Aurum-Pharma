$ErrorActionPreference = "Stop"
if ($env:CIRCLECI -ne "true") { throw "CircleCI runner required" }
./scripts/test-catalog-storage-permissions.ps1 -BackendImage aurum-backend:ci
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
