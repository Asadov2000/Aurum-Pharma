$ErrorActionPreference = "Stop"
if ($env:CIRCLECI -ne "true") { throw "CircleCI runner required" }
./scripts/benchmark-catalog-write.ps1 -BackendImage aurum-backend:ci -Rows 300
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
