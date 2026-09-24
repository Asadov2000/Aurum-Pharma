[CmdletBinding()]
param(
    [string]$BackendImage = "aurum-pharma-e2e-backend:local"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$minioImage = "docker.io/pgsty/silo:RELEASE.2026-09-16T00-00-00Z@sha256:635197cb9f36d01bee221d34d1c7d7960f6a95c48b0b6c01d99cd13bdae51a46"
$mcImage = "docker.io/pgsty/mc:RELEASE.2026-09-16T00-00-00Z@sha256:cfc83108c3abb371f8fb84d99c1fdc88f8c237e022409b0081fb7c0a3be634dd"
$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$provisionScript = (Resolve-Path (Join-Path $workspace "infra/minio/provision-catalog-reader.sh")).Path
$probeScript = (Resolve-Path (Join-Path $workspace "backend/scripts/test_catalog_storage_permissions.py")).Path
$suffix = [guid]::NewGuid().ToString("N").Substring(0, 12)
$testPrefix = "aurum-catalog-iam-test-$suffix"
$testNetwork = "$testPrefix-network"
$testContainer = "$testPrefix-minio"
$scratch = Join-Path $workspace ".codex"
[void](New-Item -ItemType Directory -Path $scratch -Force)
$testEnvFile = Join-Path $scratch "$testPrefix.env"
$networkCreated = $false
$containerCreated = $false

function Invoke-TestDocker {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$Quiet,
        [switch]$ExpectFailure
    )
    if ($Quiet) {
        & docker @Arguments *> $null
    } else {
        & docker @Arguments
    }
    if ($ExpectFailure) {
        if ($LASTEXITCODE -ne 1) {
            throw "Unsafe catalog storage provisioning must refuse with exit 1 (got $LASTEXITCODE)."
        }
    } elseif ($LASTEXITCODE -ne 0) {
        throw "Catalog storage test Docker command failed (exit $LASTEXITCODE)."
    }
}

function Invoke-Provision {
    param([switch]$ExpectFailure)
    Invoke-TestDocker -Arguments @(
        "run", "--rm", "--network", $testNetwork,
        "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
        "--env-file", $testEnvFile,
        "--mount", "type=bind,source=$provisionScript,target=/provision.sh,readonly",
        "--entrypoint", "/bin/sh", $mcImage, "/provision.sh"
    ) -ExpectFailure:$ExpectFailure
}

function Invoke-Probe {
    param([switch]$VerifyOnly)
    $arguments = @(
        "run", "--rm", "--network", $testNetwork,
        "--env-file", $testEnvFile,
        "--mount", "type=bind,source=$probeScript,target=/probe.py,readonly",
        "--entrypoint", "python", $BackendImage, "/probe.py"
    )
    if ($VerifyOnly) {
        $arguments += "--verify-only"
    }
    Invoke-TestDocker -Arguments $arguments
}

function Invoke-PolicyFixture {
    param([Parameter(Mandatory = $true)][string]$Action)
    $maintenance = @'
set -eu
mc --config-dir /tmp/mc alias set local "http://$MINIO_ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1
case "$TEST_POLICY_ACTION" in
  direct-grant) mc --config-dir /tmp/mc admin policy attach local readwrite --user "$CATALOG_MINIO_ACCESS_KEY" >/dev/null 2>&1 ;;
  direct-revoke) mc --config-dir /tmp/mc admin policy detach local readwrite --user "$CATALOG_MINIO_ACCESS_KEY" >/dev/null 2>&1 ;;
  group-grant)
    mc --config-dir /tmp/mc admin group add local catalog-iam-test "$CATALOG_MINIO_ACCESS_KEY" >/dev/null 2>&1
    mc --config-dir /tmp/mc admin policy attach local readwrite --group catalog-iam-test >/dev/null 2>&1 ;;
  group-revoke)
    mc --config-dir /tmp/mc admin policy detach local readwrite --group catalog-iam-test >/dev/null 2>&1
    mc --config-dir /tmp/mc admin group remove local catalog-iam-test "$CATALOG_MINIO_ACCESS_KEY" >/dev/null 2>&1
    mc --config-dir /tmp/mc admin group remove local catalog-iam-test >/dev/null 2>&1 ;;
  *) exit 2 ;;
esac
mc --config-dir /tmp/mc --no-color admin user info local "$CATALOG_MINIO_ACCESS_KEY" |
while IFS= read -r line; do
  case "$line" in PolicyName:*|MemberOf:*) printf '%s\n' "$line" ;; esac
done
'@
    $maintenance = $maintenance.Replace("`r`n", "`n")
    Invoke-TestDocker -Arguments @(
        "run", "--rm", "--network", $testNetwork,
        "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
        "--env-file", $testEnvFile, "--env", "TEST_POLICY_ACTION=$Action",
        "--entrypoint", "/bin/sh", $mcImage, "-ec", $maintenance
    )
}

try {
    # Synthetic credentials are scoped to this disposable MinIO instance.
    $values = @(
        "MINIO_ENDPOINT=${testContainer}:9000",
        "MINIO_BUCKET=$testPrefix",
        "MINIO_SECURE=false",
        "MINIO_ROOT_USER=root-$suffix",
        "MINIO_ROOT_PASSWORD=$([guid]::NewGuid().ToString('N'))",
        "CATALOG_MINIO_ACCESS_KEY=catalog-$suffix",
        "CATALOG_MINIO_SECRET_KEY=$([guid]::NewGuid().ToString('N'))",
        "MINIO_BACKUP_ACCESS_KEY=backup-$suffix",
        "CATALOG_STORAGE_TEST_ISOLATED=true"
    )
    [IO.File]::WriteAllLines($testEnvFile, $values, [Text.UTF8Encoding]::new($false))
    Invoke-TestDocker -Arguments @("network", "create", "--internal", $testNetwork) -Quiet
    $networkCreated = $true
    Invoke-TestDocker -Arguments @(
        "run", "-d", "--name", $testContainer, "--network", $testNetwork,
        "--env-file", $testEnvFile,
        "--tmpfs", "/data:rw,noexec,nosuid,size=64m",
        $minioImage, "server", "/data"
    ) -Quiet
    $containerCreated = $true
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        & docker exec $testContainer curl -fsS http://127.0.0.1:9000/minio/health/live *> $null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        throw "Isolated MinIO did not become ready."
    }
    Invoke-Provision
    Invoke-Probe
    Invoke-Provision
    Invoke-Probe -VerifyOnly
    Write-Host "Repeated provisioning: permissions and existing data preserved."
    foreach ($scope in @("direct", "group")) {
        Invoke-PolicyFixture -Action "$scope-grant"
        Invoke-Provision -ExpectFailure
        Write-Host "Overprivileged $scope identity: provisioning refused."
        Invoke-PolicyFixture -Action "$scope-revoke"
        Invoke-Provision
        Invoke-Probe -VerifyOnly
        Write-Host "Operator resolved $scope privilege: read-only IAM and existing data verified."
    }
    Write-Host "Catalog storage IAM: allowed reads, denied operations and preserved data verified."
} finally {
    # Exact names generated above; no shared containers, volumes or databases are touched.
    if ($containerCreated) {
        & docker rm -f $testContainer *> $null
    }
    if ($networkCreated) {
        & docker network rm $testNetwork *> $null
    }
    if (Test-Path -LiteralPath $testEnvFile) {
        Remove-Item -LiteralPath $testEnvFile
    }
}
