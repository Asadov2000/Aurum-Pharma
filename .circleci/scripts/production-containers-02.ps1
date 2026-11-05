$ErrorActionPreference = "Stop"
if ($env:CIRCLECI -ne "true") { throw "CircleCI runner required" }
./scripts/test-production-internal-tls.ps1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
