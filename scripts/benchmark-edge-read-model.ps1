[CmdletBinding()]
param(
    [string]$BackendImage = "aurum-pharma-e2e-backend:local",
    [ValidateRange(1, 100000)][int]$CatalogRows = 100000,
    [ValidateRange(1, 250000)][int]$InventoryRows = 250000,
    [ValidateRange(5, 200)][int]$Repetitions = 30
)

# Every resource is unique, internal-only and disposable. No shared Compose
# project, host port, named volume, .env file or development database is used.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backend = Join-Path $workspace "backend"
$infra = Join-Path $workspace "infra"
$scratch = Join-Path $workspace ".codex"
[void](New-Item -ItemType Directory -Path $scratch -Force)
$prefix = "aurum-edge-read-benchmark-$([guid]::NewGuid().ToString('N').Substring(0,12))"
$network = "$prefix-network"
$containers = [Collections.Generic.List[string]]::new()
$temporaryFiles = [Collections.Generic.List[string]]::new()
$networkCreated = $false
$postgresImage = "postgres:16-alpine@sha256:4327b9fd295502f326f44153a1045a7170ddbfffed1c3829798328556cfd09e2"
$sourceMounts = @()
foreach ($source in @("app", "scripts", "alembic", "alembic.ini")) {
    $sourceMounts += @("--mount", "type=bind,source=$backend/$source,target=/app/$source,readonly")
}

function New-BenchmarkSecret {
    return [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()
}

function Write-BenchmarkEnvironment {
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

function Invoke-BenchmarkDocker {
    param([string[]]$Arguments, [switch]$Quiet)
    $logPath = Join-Path $scratch "$prefix-step.log"
    if ($Quiet) { & docker @Arguments *> $logPath } else { & docker @Arguments }
    if ($LASTEXITCODE -ne 0) {
        throw "Isolated Edge read benchmark Docker step failed (exit $LASTEXITCODE); diagnostic: $logPath"
    }
}

try {
    Invoke-BenchmarkDocker -Arguments @("image", "inspect", $BackendImage) -Quiet
    $database = @{
        POSTGRES_HOST = "edge-benchmark-postgres"
        POSTGRES_DB = "aurum_edge_read_benchmark_test"
        POSTGRES_USER = "postgres"
        POSTGRES_PASSWORD = (New-BenchmarkSecret)
    }
    foreach ($role in @("APP", "SUPPORT", "MAILER", "BILLING_WORKER", "WORKER", "CATALOG_WORKER", "MIGRATOR", "BACKUP", "PITR")) {
        $database["AURUM_${role}_PASSWORD"] = New-BenchmarkSecret
    }
    $databaseEnv = Write-BenchmarkEnvironment "database" $database
    $controller = @{
        ENVIRONMENT = "development"
        DEPLOYMENT_PROFILE = "edge_shadow"
        PYTHONDONTWRITEBYTECODE = "1"
        PYTHONUNBUFFERED = "1"
        DATABASE_URL_APP = "postgresql+asyncpg://aurum_app:$($database.AURUM_APP_PASSWORD)@edge-benchmark-postgres:5432/aurum_edge_read_benchmark_test"
        DATABASE_URL_SUPPORT = "postgresql+asyncpg://aurum_support:$($database.AURUM_SUPPORT_PASSWORD)@edge-benchmark-postgres:5432/aurum_edge_read_benchmark_test"
        DATABASE_URL_MIGRATION = "postgresql+asyncpg://aurum_migrator:$($database.AURUM_MIGRATOR_PASSWORD)@edge-benchmark-postgres:5432/aurum_edge_read_benchmark_test"
    }
    $controllerEnv = Write-BenchmarkEnvironment "controller" $controller

    Invoke-BenchmarkDocker -Arguments @("network", "create", "--internal", $network) -Quiet
    $networkCreated = $true
    $postgresName = "$prefix-postgres"
    $containers.Add($postgresName)
    Invoke-BenchmarkDocker -Arguments @(
        "run", "--detach", "--name", $postgresName, "--network", $network,
        "--network-alias", "edge-benchmark-postgres", "--env-file", $databaseEnv,
        "--mount", "type=bind,source=$infra/postgres/init.sh,target=/docker-entrypoint-initdb.d/init.sh,readonly",
        "--mount", "type=bind,source=$infra/postgres/role-contract.sql,target=/docker-entrypoint-initdb.d/role-contract.inc,readonly",
        "--memory=8g", "--memory-swap=8g", "--cpus=4", "--shm-size=512m", $postgresImage,
        "-c", "shared_buffers=512MB"
    ) -Quiet

    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        & docker exec $postgresName pg_isready -U postgres -d $database.POSTGRES_DB *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw "Isolated Edge benchmark PostgreSQL did not become healthy." }

    Invoke-BenchmarkDocker -Arguments @(
        "run", "--rm", "--network", $network, "--env-file", $databaseEnv,
        "--mount", "type=bind,source=$infra/postgres,target=/bootstrap,readonly",
        "--entrypoint", "/bin/sh", $postgresImage, "/bootstrap/bootstrap-roles.sh"
    ) -Quiet
    Invoke-BenchmarkDocker -Arguments @(
        "run", "--rm", "--network", $network, "--env-file", $controllerEnv;
        $sourceMounts; "--workdir", "/app", "--entrypoint", "python", $BackendImage,
        "-m", "app.migrate", "legacy-upgrade", "0029"
    ) -Quiet
    Invoke-BenchmarkDocker -Arguments @(
        "run", "--rm", "--network", $network, "--env-file", $databaseEnv,
        "--mount", "type=bind,source=$infra/postgres,target=/bootstrap,readonly",
        "--entrypoint", "/bin/sh", $postgresImage, "/bootstrap/bootstrap-roles.sh"
    ) -Quiet
    Invoke-BenchmarkDocker -Arguments @(
        "run", "--rm", "--network", $network, "--env-file", $databaseEnv,
        "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "MIGRATION_DB_HOST=edge-benchmark-postgres",
        "--env", "MIGRATION_DB_NAME=aurum_edge_read_benchmark_test";
        $sourceMounts; "--workdir", "/app", "--entrypoint", "python", $BackendImage,
        "-m", "app.db_owner_migrate"
    ) -Quiet
    Invoke-BenchmarkDocker -Arguments @(
        "run", "--rm", "--network", $network, "--env-file", $databaseEnv,
        "--mount", "type=bind,source=$infra/postgres,target=/bootstrap,readonly",
        "--entrypoint", "/bin/sh", $postgresImage, "/bootstrap/bootstrap-roles.sh"
    ) -Quiet
    Invoke-BenchmarkDocker -Arguments @(
        "run", "--rm", "--network", $network, "--env-file", $controllerEnv;
        $sourceMounts; "--workdir", "/app", "--entrypoint", "python", $BackendImage,
        "-m", "app.migrate", "upgrade", "head"
    ) -Quiet

    $revision = (& git -C $workspace rev-parse HEAD).Trim()
    $imageId = (& docker image inspect --format '{{.Id}}' $BackendImage).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Cannot identify benchmark image." }
    [ordered]@{
        benchmark = "edge_read_model"
        revision = $revision
        backend_image_id = $imageId
        catalog_rows = $CatalogRows
        inventory_rows = $InventoryRows
        repetitions = $Repetitions
        postgres_cpu = 4
        postgres_memory_bytes = 8589934592
        isolated = $true
    } | ConvertTo-Json -Compress

    $driverName = "$prefix-driver"
    $containers.Add($driverName)
    & docker run --name $driverName --network $network --env-file $controllerEnv `
        @sourceMounts --workdir /app --memory=1g --memory-swap=1g --cpus=1 `
        --entrypoint python $BackendImage scripts/benchmark_edge_read_model.py `
        --catalog-rows $CatalogRows --inventory-rows $InventoryRows --repetitions $Repetitions
    if ($LASTEXITCODE -ne 0) { throw "Edge read benchmark failed; see the fixed numeric reason." }
}
finally {
    $cleanupFailed = $false
    $remainingNames = @(& docker ps --all --format '{{.Names}}')
    if ($LASTEXITCODE -ne 0) { $cleanupFailed = $true }
    for ($index = $containers.Count - 1; $index -ge 0; $index--) {
        if ($remainingNames -contains $containers[$index]) {
            & docker rm --force --volumes $containers[$index] *> $null
            if ($LASTEXITCODE -ne 0) { $cleanupFailed = $true }
        }
    }
    if ($networkCreated) {
        & docker network rm $network *> $null
        if ($LASTEXITCODE -ne 0) { $cleanupFailed = $true }
    }
    foreach ($path in $temporaryFiles) {
        try { Remove-Item -LiteralPath $path -Force -ErrorAction Stop }
        catch { $cleanupFailed = $true }
    }
    if ($cleanupFailed) {
        throw "Isolated Edge benchmark cleanup failed; inspect resources with prefix $prefix."
    }
}
