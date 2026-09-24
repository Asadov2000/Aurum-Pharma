[CmdletBinding()]
param(
    [string]$BackendImage = "aurum-pharma-e2e-backend:local",
    [switch]$VerifyMigration0151Downgrade
)

# Disposable read-only Edge smoke. Unique labels and exact ownership checks
# guard cleanup; no published port, named volume, shared Compose project or
# access to the host's development/demo database.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backend = Join-Path $workspace "backend"
$infra = Join-Path $workspace "infra"
$scratch = Join-Path $workspace ".codex"
[void](New-Item -ItemType Directory -Path $scratch -Force)
$prefix = "aurum-edge-smoke-$([guid]::NewGuid().ToString('N').Substring(0,12))"
$network = "$prefix-network"
$label = "tj.aurum.edge.smoke=$prefix"
$containers = [Collections.Generic.List[string]]::new()
$temporaryFiles = [Collections.Generic.List[string]]::new()
$networkCreated = $false
$postgresImage = "postgres:16-alpine@sha256:4327b9fd295502f326f44153a1045a7170ddbfffed1c3829798328556cfd09e2"
$sourceMounts = @()
foreach ($source in @("app", "scripts", "alembic", "alembic.ini")) {
    $sourceMounts += @("--mount", "type=bind,source=$backend/$source,target=/app/$source,readonly")
}

function New-SmokeSecret {
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) }
    finally { $generator.Dispose() }
    return [BitConverter]::ToString($bytes).Replace("-", "").ToLowerInvariant()
}

function Write-SmokeEnvironment {
    param([string]$Name, [hashtable]$Values)
    $path = Join-Path $scratch "$prefix-$Name.env"
    $temporaryFiles.Add($path)
    [IO.File]::WriteAllLines(
        $path,
        [string[]]@($Values.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }),
        [Text.UTF8Encoding]::new($false)
    )
    return $path
}

function Invoke-SmokeDocker {
    param([string[]]$Arguments, [switch]$Quiet)
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        if ($Quiet) { & docker @Arguments *> $null }
        else { & docker @Arguments }
        $code = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    if ($code -ne 0) { throw "Isolated Edge smoke Docker step failed (exit $code)." }
}

function Test-OwnedResource {
    param([string]$Kind, [string]$Name)
    if (-not $Name.StartsWith("$prefix-", [StringComparison]::Ordinal)) {
        throw "Refusing cleanup of a resource outside the invocation prefix."
    }
    $format = if ($Kind -eq "container") { '{{json .Config.Labels}}' }
    else { '{{json .Labels}}' }
    $rawLabels = (& docker $Kind inspect --format $format $Name).Trim()
    $actual = ($rawLabels | ConvertFrom-Json).'tj.aurum.edge.smoke'
    if ($LASTEXITCODE -ne 0 -or $actual -cne $prefix) {
        throw "Refusing cleanup of an unowned isolated resource."
    }
}

function Invoke-Driver {
    param([string]$Phase)
    $arguments = @("run", "--rm", "--network", $network, "--env-file", $controllerEnv, "--label", $label)
    $arguments += $sourceMounts
    $arguments += @("--workdir", "/app", "--entrypoint", "python", $BackendImage, "scripts/smoke_edge_cold_read.py", $Phase)
    Invoke-SmokeDocker -Arguments $arguments
}

function Wait-Health {
    param([int]$Status, [string]$Edge)
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & docker exec $apiName python /app/scripts/smoke_edge_cold_read.py health --expected $Status --expected-edge $Edge *> $null
            $code = $LASTEXITCODE
        }
        finally { $ErrorActionPreference = $previousPreference }
        if ($code -eq 0) {
            Write-Host "health=$Status edge_snapshot=$Edge"
            return
        }
        Start-Sleep -Seconds 1
    }
    throw "Isolated Edge API did not reach expected health $Status/$Edge."
}

try {
    Invoke-SmokeDocker -Arguments @("image", "inspect", $BackendImage) -Quiet
    $database = @{
        POSTGRES_HOST = "edge-benchmark-postgres"
        POSTGRES_DB = "aurum_edge_read_benchmark_test"
        POSTGRES_USER = "postgres"
        POSTGRES_PASSWORD = New-SmokeSecret
    }
    foreach ($role in @("APP", "SUPPORT", "MAILER", "BILLING_WORKER", "WORKER", "CATALOG_WORKER", "MIGRATOR", "BACKUP", "PITR")) {
        $database["AURUM_${role}_PASSWORD"] = New-SmokeSecret
    }
    $databaseEnv = Write-SmokeEnvironment "database" $database
    $controller = @{
        ENVIRONMENT = "development"
        DEPLOYMENT_PROFILE = "edge_shadow"
        PYTHONDONTWRITEBYTECODE = "1"
        PYTHONUNBUFFERED = "1"
        DATABASE_URL_APP = "postgresql+asyncpg://aurum_app:$($database.AURUM_APP_PASSWORD)@edge-benchmark-postgres:5432/aurum_edge_read_benchmark_test"
        DATABASE_URL_SUPPORT = "postgresql+asyncpg://aurum_support:$($database.AURUM_SUPPORT_PASSWORD)@edge-benchmark-postgres:5432/aurum_edge_read_benchmark_test"
        DATABASE_URL_MIGRATION = "postgresql+asyncpg://aurum_migrator:$($database.AURUM_MIGRATOR_PASSWORD)@edge-benchmark-postgres:5432/aurum_edge_read_benchmark_test"
        REDIS_URL = "redis://edge-smoke-redis:6379/0"
        JWT_SECRET = New-SmokeSecret
        EDGE_SYNC_CREDENTIAL = "edge_v1.$([guid]::NewGuid()).$(New-SmokeSecret)"
    }
    $controllerEnv = Write-SmokeEnvironment "controller" $controller
    Invoke-SmokeDocker -Arguments @("network", "create", "--internal", "--label", $label, $network) -Quiet
    $networkCreated = $true
    $isInternal = (& docker network inspect --format '{{.Internal}}' $network).Trim()
    if ($LASTEXITCODE -ne 0 -or $isInternal -cne "true") {
        throw "Isolated Edge smoke network is not internal."
    }

    $postgresName = "$prefix-postgres"
    $containers.Add($postgresName)
    Invoke-SmokeDocker -Arguments @(
        "run", "--detach", "--name", $postgresName, "--label", $label,
        "--network", $network, "--network-alias", "edge-benchmark-postgres",
        "--env-file", $databaseEnv,
        "--mount", "type=bind,source=$infra/postgres/init.sh,target=/docker-entrypoint-initdb.d/init.sh,readonly",
        "--mount", "type=bind,source=$infra/postgres/role-contract.sql,target=/docker-entrypoint-initdb.d/role-contract.inc,readonly",
        "--memory=1g", "--cpus=1", $postgresImage
    ) -Quiet
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & docker exec $postgresName pg_isready -U postgres -d $database.POSTGRES_DB *> $null
            $code = $LASTEXITCODE
        }
        finally { $ErrorActionPreference = $previousPreference }
        if ($code -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw "Isolated Edge PostgreSQL did not become ready." }

    $redisName = "$prefix-redis"
    $containers.Add($redisName)
    Invoke-SmokeDocker -Arguments @(
        "run", "--detach", "--name", $redisName, "--label", $label,
        "--network", $network, "--network-alias", "edge-smoke-redis",
        "--memory=128m", "redis:7-alpine", "redis-server", "--save", "", "--appendonly", "no"
    ) -Quiet
    Invoke-SmokeDocker -Arguments @(
        "run", "--rm", "--network", $network, "--label", $label, "--env-file", $databaseEnv,
        "--mount", "type=bind,source=$infra/postgres,target=/bootstrap,readonly",
        "--entrypoint", "/bin/sh", $postgresImage, "/bootstrap/bootstrap-roles.sh"
    ) -Quiet
    $arguments = @("run", "--rm", "--network", $network, "--label", $label, "--env-file", $controllerEnv)
    $arguments += $sourceMounts
    $arguments += @("--workdir", "/app", "--entrypoint", "python", $BackendImage, "-m", "app.migrate", "legacy-upgrade", "0029")
    Invoke-SmokeDocker -Arguments $arguments -Quiet
    Invoke-SmokeDocker -Arguments @(
        "run", "--rm", "--network", $network, "--label", $label, "--env-file", $databaseEnv,
        "--mount", "type=bind,source=$infra/postgres,target=/bootstrap,readonly",
        "--entrypoint", "/bin/sh", $postgresImage, "/bootstrap/bootstrap-roles.sh"
    ) -Quiet
    $arguments = @(
        "run", "--rm", "--network", $network, "--label", $label, "--env-file", $databaseEnv,
        "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "MIGRATION_DB_HOST=edge-benchmark-postgres",
        "--env", "MIGRATION_DB_NAME=aurum_edge_read_benchmark_test"
    )
    $arguments += $sourceMounts
    $arguments += @("--workdir", "/app", "--entrypoint", "python", $BackendImage, "-m", "app.db_owner_migrate")
    Invoke-SmokeDocker -Arguments $arguments -Quiet
    Invoke-SmokeDocker -Arguments @(
        "run", "--rm", "--network", $network, "--label", $label, "--env-file", $databaseEnv,
        "--mount", "type=bind,source=$infra/postgres,target=/bootstrap,readonly",
        "--entrypoint", "/bin/sh", $postgresImage, "/bootstrap/bootstrap-roles.sh"
    ) -Quiet
    $arguments = @("run", "--rm", "--network", $network, "--label", $label, "--env-file", $controllerEnv)
    $arguments += $sourceMounts
    $arguments += @("--workdir", "/app", "--entrypoint", "python", $BackendImage, "-m", "app.migrate", "upgrade", "head")
    Invoke-SmokeDocker -Arguments $arguments -Quiet
    if ($VerifyMigration0151Downgrade) {
        $downgradeArguments = @("run", "--rm", "--network", $network, "--label", $label, "--env-file", $controllerEnv)
        $downgradeArguments += $sourceMounts
        $downgradeArguments += @(
            "--workdir", "/app", "--entrypoint", "python", $BackendImage,
            "-m", "app.migrate", "downgrade", "0150", "--allow-test-downgrade"
        )
        Invoke-SmokeDocker -Arguments $downgradeArguments -Quiet
        Invoke-SmokeDocker -Arguments $arguments -Quiet
        Write-Host "migration_0151_downgrade_upgrade=ok"
    }

    $apiName = "$prefix-api"
    $containers.Add($apiName)
    $arguments = @("run", "--detach", "--name", $apiName, "--label", $label, "--network", $network, "--env-file", $controllerEnv)
    $arguments += $sourceMounts
    $arguments += @("--workdir", "/app", "--memory=1g", "--cpus=1", $BackendImage, "python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000")
    Invoke-SmokeDocker -Arguments $arguments -Quiet
    Wait-Health -Status 503 -Edge "false"
    Invoke-Driver -Phase "seed"
    Wait-Health -Status 200 -Edge "true"
    Invoke-Driver -Phase "read"

    Invoke-SmokeDocker -Arguments @("stop", $postgresName) -Quiet
    Wait-Health -Status 503 -Edge "any"
    Invoke-SmokeDocker -Arguments @("start", $postgresName) -Quiet
    Wait-Health -Status 200 -Edge "true"
    Invoke-SmokeDocker -Arguments @("restart", $apiName) -Quiet
    Wait-Health -Status 200 -Edge "true"
    Invoke-Driver -Phase "read"
    Write-Host "[OK] Isolated Edge read-only cold/WAN-absent/restart smoke passed."
}
finally {
    $cleanupFailed = $false
    for ($index = $containers.Count - 1; $index -ge 0; $index--) {
        $name = $containers[$index]
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & docker container inspect $name *> $null
            $code = $LASTEXITCODE
        }
        finally { $ErrorActionPreference = $previousPreference }
        if ($code -eq 0) {
            try {
                Test-OwnedResource -Kind "container" -Name $name
                Invoke-SmokeDocker -Arguments @("rm", "--force", "--volumes", $name) -Quiet
            }
            catch { $cleanupFailed = $true }
        }
    }
    if ($networkCreated) {
        try {
            Test-OwnedResource -Kind "network" -Name $network
            Invoke-SmokeDocker -Arguments @("network", "rm", $network) -Quiet
        }
        catch { $cleanupFailed = $true }
    }
    foreach ($path in $temporaryFiles) {
        $resolved = [IO.Path]::GetFullPath($path)
        if (-not $resolved.StartsWith("$scratch$([IO.Path]::DirectorySeparatorChar)$prefix-", [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing cleanup of a file outside the isolated scratch prefix."
        }
        try { Remove-Item -LiteralPath $resolved -Force -ErrorAction Stop }
        catch { $cleanupFailed = $true }
    }
    if ($cleanupFailed) {
        throw "Isolated Edge smoke cleanup failed; inspect resources labelled $label."
    }
}
