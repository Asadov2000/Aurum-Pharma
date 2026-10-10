$ErrorActionPreference = "Stop"
if ($env:CIRCLECI -ne "true") { throw "CircleCI runner required" }
./scripts/smoke-edge-cold-read.ps1 -BackendImage aurum-backend:ci -BackupImage aurum-pharma-backup:ci -VerifyRestore

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
