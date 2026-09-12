[CmdletBinding()]
param(
    [string]$BackendImage = "aurum-pharma-e2e-backend:local",
    [ValidateRange(1, 50000)][int]$Rows = 15000,
    [ValidateSet("csv", "xlsx")][string]$Format = "csv",
    [ValidatePattern('^(create|skip|update|create_copy)(,(create|skip|update|create_copy))*$')]
    [string]$Cases = "create,skip,update"
)

# No shared Compose project, .env, host ports or existing volumes are used.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backend = Join-Path $workspace "backend"
$infra = Join-Path $workspace "infra"
$scratch = Join-Path $workspace ".codex"
[void](New-Item -ItemType Directory -Path $scratch -Force)
$prefix = "aurum-catalog-benchmark-$([guid]::NewGuid().ToString('N').Substring(0,12))"
$network = "$prefix-network"
$containers = [Collections.Generic.List[string]]::new()
$envFiles = [Collections.Generic.List[string]]::new()
$networkCreated = $false
$sourceMounts = @()
foreach ($source in @("app", "scripts", "alembic", "alembic.ini")) {
    $sourceMounts += @("--mount", "type=bind,source=$backend/$source,target=/app/$source,readonly")
}
$postgresImage = "postgres:16-alpine@sha256:4327b9fd295502f326f44153a1045a7170ddbfffed1c3829798328556cfd09e2"
$redisImage = "redis:7-alpine@sha256:02f2cc4882f8bf87c79a220ac958f58c700bdec0dfb9b9ea61b62fb0e8f1bfcf"
$minioImage = "quay.io/minio/minio:latest@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e"
$mcImage = "quay.io/minio/mc@sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727"

function New-BenchmarkSecret {
    return [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()
}

function Write-BenchmarkEnvironment {
    param([string]$Name, [hashtable]$Values)
    $path = Join-Path $scratch "$prefix-$Name.env"
    $envFiles.Add($path)
    [IO.File]::WriteAllLines($path, [string[]]@($Values.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }), [Text.UTF8Encoding]::new($false))
    return $path
}

function Invoke-BenchmarkDocker {
    param([string[]]$Arguments, [switch]$Quiet)
    if ($Arguments[0] -eq "run" -and $Arguments -contains "--rm") {
        $stepName = "$prefix-step-$($containers.Count)"
        $containers.Add($stepName)
        $Arguments = @("run", "--name", $stepName) + $Arguments[1..($Arguments.Count - 1)]
    }
    $stepLog = Join-Path $scratch "$prefix-step.log"
    if ($Quiet) { & docker @Arguments *> $stepLog } else { & docker @Arguments }
    if ($LASTEXITCODE -ne 0) { throw "Isolated catalog benchmark Docker step failed (exit $LASTEXITCODE); diagnostic: $stepLog" }
}

function Start-BenchmarkContainer {
    param([string]$Name, [string[]]$Arguments)
    # Track before create so cleanup also covers a failed start.
    $containers.Add($Name)
    Invoke-BenchmarkDocker -Arguments (@("run", "--detach", "--name", $Name, "--network", $network) + $Arguments) -Quiet
}

try {
    # Check the supplied image exists; do not silently install a backend runtime.
    Invoke-BenchmarkDocker -Arguments @("image", "inspect", $BackendImage) -Quiet
    $db = @{
        POSTGRES_HOST = "benchmark-postgres"; POSTGRES_DB = "aurum_catalog_benchmark_test"
        POSTGRES_USER = "postgres"; POSTGRES_PASSWORD = (New-BenchmarkSecret)
    }
    foreach ($role in @("APP", "SUPPORT", "MAILER", "BILLING_WORKER", "WORKER", "CATALOG_WORKER", "MIGRATOR", "BACKUP", "PITR")) {
        $db["AURUM_${role}_PASSWORD"] = New-BenchmarkSecret
    }
    $dbEnv = Write-BenchmarkEnvironment "db" $db
    $redis = @{ REDIS_PASSWORD = (New-BenchmarkSecret) }
    foreach ($role in @("API", "BEAT", "SYSTEM", "CATALOG", "BILLING", "MAILER")) {
        $redis["REDIS_CELERY_${role}_PASSWORD"] = New-BenchmarkSecret
    }
    $redisEnv = Write-BenchmarkEnvironment "redis" $redis
    $storage = @{
        MINIO_ENDPOINT = "benchmark-minio:9000"; MINIO_BUCKET = "catalog-benchmark"; MINIO_SECURE = "false"
        MINIO_ROOT_USER = "benchmark_root"; MINIO_ROOT_PASSWORD = (New-BenchmarkSecret)
        CATALOG_MINIO_ACCESS_KEY = "benchmark_catalog_reader"; CATALOG_MINIO_SECRET_KEY = (New-BenchmarkSecret)
    }
    $storageEnv = Write-BenchmarkEnvironment "storage" $storage
    $controller = @{
        ENVIRONMENT = "development"; PYTHONDONTWRITEBYTECODE = "1"; PYTHONUNBUFFERED = "1"
        DATABASE_URL_APP = "postgresql+asyncpg://aurum_app:$($db.AURUM_APP_PASSWORD)@benchmark-postgres:5432/aurum_catalog_benchmark_test"
        DATABASE_URL_SUPPORT = "postgresql+asyncpg://aurum_support:$($db.AURUM_SUPPORT_PASSWORD)@benchmark-postgres:5432/aurum_catalog_benchmark_test"
        DATABASE_URL_MIGRATION = "postgresql+asyncpg://aurum_migrator:$($db.AURUM_MIGRATOR_PASSWORD)@benchmark-postgres:5432/aurum_catalog_benchmark_test"
        CELERY_BROKER_URL = "redis://aurum_api:$($redis.REDIS_CELERY_API_PASSWORD)@benchmark-redis:6379/0"
        REDIS_URL = "redis://:$($redis.REDIS_PASSWORD)@benchmark-redis:6379/0"
        MINIO_ENDPOINT = $storage.MINIO_ENDPOINT; MINIO_BUCKET = $storage.MINIO_BUCKET; MINIO_SECURE = "false"
        MINIO_ROOT_USER = $storage.MINIO_ROOT_USER; MINIO_ROOT_PASSWORD = $storage.MINIO_ROOT_PASSWORD
    }
    $controllerEnv = Write-BenchmarkEnvironment "controller" $controller
    $worker = @{
        ENVIRONMENT = "development"; PYTHONDONTWRITEBYTECODE = "1"; PYTHONUNBUFFERED = "1"
        DATABASE_URL_CATALOG_WORKER = "postgresql+asyncpg://aurum_catalog_worker:$($db.AURUM_CATALOG_WORKER_PASSWORD)@benchmark-postgres:5432/aurum_catalog_benchmark_test"
        CELERY_BROKER_URL = "redis://aurum_catalog:$($redis.REDIS_CELERY_CATALOG_PASSWORD)@benchmark-redis:6379/0"
        MINIO_ENDPOINT = $storage.MINIO_ENDPOINT; MINIO_BUCKET = $storage.MINIO_BUCKET; MINIO_SECURE = "false"
        CATALOG_MINIO_ACCESS_KEY = $storage.CATALOG_MINIO_ACCESS_KEY; CATALOG_MINIO_SECRET_KEY = $storage.CATALOG_MINIO_SECRET_KEY
    }
    $workerEnv = Write-BenchmarkEnvironment "worker" $worker
    Invoke-BenchmarkDocker -Arguments @("network", "create", "--internal", $network) -Quiet
    $networkCreated = $true
    $postgresName = "$prefix-postgres"
    Start-BenchmarkContainer $postgresName @(
        "--network-alias", "benchmark-postgres", "--env-file", $dbEnv,
        "--mount", "type=bind,source=$infra/postgres/init.sh,target=/docker-entrypoint-initdb.d/init.sh,readonly",
        "--mount", "type=bind,source=$infra/postgres/role-contract.sql,target=/docker-entrypoint-initdb.d/role-contract.inc,readonly",
        "--memory=2g", "--memory-swap=2g", "--cpus=2", "--shm-size=256m", $postgresImage
    )
    $redisName = "$prefix-redis"
    Start-BenchmarkContainer $redisName @(
        "--network-alias", "benchmark-redis", "--env-file", $redisEnv,
        "--memory=512m", "--memory-swap=512m", "--cpus=1", "--tmpfs", "/data:rw,size=64m",
        "--mount", "type=bind,source=$infra/redis/start-with-worker-acl.sh,target=/start.sh,readonly",
        "--entrypoint", "/bin/sh", $redisImage, "/start.sh"
    )
    Start-BenchmarkContainer "$prefix-minio" @(
        "--network-alias", "benchmark-minio", "--env-file", $storageEnv,
        "--memory=1g", "--memory-swap=1g", "--cpus=1", "--tmpfs", "/data:rw,size=128m",
        $minioImage, "server", "/data"
    )
    Invoke-BenchmarkDocker -Arguments @(
        "run", "--rm", "--network", $network, "--env-file", $dbEnv,
        "--mount", "type=bind,source=$infra/postgres,target=/bootstrap,readonly",
        "--entrypoint", "/bin/sh", $postgresImage, "/bootstrap/bootstrap-roles.sh"
    ) -Quiet
    Invoke-BenchmarkDocker -Arguments @(
        "run", "--rm", "--network", $network, "--env-file", $controllerEnv;
        $sourceMounts; "--workdir", "/app",
        "--entrypoint", "python", $BackendImage, "-m", "app.migrate", "legacy-upgrade", "0029"
    ) -Quiet
    Invoke-BenchmarkDocker -Arguments @(
        "run", "--rm", "--network", $network, "--env-file", $dbEnv,
        "--mount", "type=bind,source=$infra/postgres,target=/bootstrap,readonly",
        "--entrypoint", "/bin/sh", $postgresImage, "/bootstrap/bootstrap-roles.sh"
    ) -Quiet
    Invoke-BenchmarkDocker -Arguments @(
        "run", "--rm", "--network", $network, "--env-file", $dbEnv,
        "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "MIGRATION_DB_HOST=benchmark-postgres",
        "--env", "MIGRATION_DB_NAME=aurum_catalog_benchmark_test";
        $sourceMounts; "--workdir", "/app",
        "--entrypoint", "python", $BackendImage, "-m", "app.db_owner_migrate"
    ) -Quiet
    Invoke-BenchmarkDocker -Arguments @(
        "run", "--rm", "--network", $network, "--env-file", $dbEnv,
        "--mount", "type=bind,source=$infra/postgres,target=/bootstrap,readonly",
        "--entrypoint", "/bin/sh", $postgresImage, "/bootstrap/bootstrap-roles.sh"
    ) -Quiet
    Invoke-BenchmarkDocker -Arguments @(
        "run", "--rm", "--network", $network, "--env-file", $controllerEnv;
        $sourceMounts; "--workdir", "/app",
        "--entrypoint", "python", $BackendImage, "-m", "app.migrate", "upgrade", "head"
    ) -Quiet
    Invoke-BenchmarkDocker -Arguments @(
        "run", "--rm", "--network", $network, "--env-file", $storageEnv,
        "--mount", "type=bind,source=$infra/minio/provision-catalog-reader.sh,target=/provision.sh,readonly",
        "--entrypoint", "/bin/sh", $mcImage, "/provision.sh"
    ) -Quiet
    $workerName = "$prefix-worker"
    Start-BenchmarkContainer $workerName @(
        "--env-file", $workerEnv, "--memory=768m", "--memory-swap=768m", "--cpus=1", "--pids-limit=128";
        $sourceMounts; "--workdir", "/app",
        "--entrypoint", "python", $BackendImage, "-m", "celery", "-A", "app.tasks.catalog_app", "worker",
        "--loglevel=warning", "-Q", "catalog-worker", "--concurrency=1", "--without-gossip",
        "--without-mingle", "--without-heartbeat", "--pidfile=/tmp/celery-worker.pid"
    )
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        & docker exec $workerName python -m app.tasks.worker_health catalog /tmp/celery-worker.pid *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw "Isolated catalog worker did not become healthy." }
    $revision = (& git -C $workspace rev-parse HEAD).Trim()
    $imageId = (& docker image inspect --format '{{.Id}}' $BackendImage).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Cannot identify benchmark image." }
    $sourceFiles = @(
        Get-ChildItem (Join-Path $backend "app"), (Join-Path $backend "scripts"), (Join-Path $backend "alembic") -Recurse -File -Filter *.py
        Get-Item (Join-Path $backend "alembic.ini"), $PSCommandPath
    ) | Sort-Object FullName
    $manifest = ($sourceFiles | ForEach-Object {
        "$([IO.Path]::GetRelativePath($workspace, $_.FullName).Replace('\','/')) $((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)"
    }) -join "`n"
    $sourceHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($manifest))).ToLowerInvariant()
    [ordered]@{
        benchmark = "catalog_write"; revision = $revision; rows = $Rows; format = $Format
        backend_image_id = $imageId; source_sha256 = $sourceHash
        worker_memory_bytes = 805306368; worker_cpu = 1; postgres_memory_bytes = 2147483648
        postgres_cpu = 2; isolated = $true
    } | ConvertTo-Json -Compress
    $driverName = "$prefix-driver"
    $containers.Add($driverName)
    & docker run --name $driverName --network $network --env-file $controllerEnv `
        @sourceMounts --workdir /app `
        --entrypoint python $BackendImage scripts/benchmark_catalog_write.py `
        --rows $Rows --format $Format --cases $Cases
    $benchmarkExit = $LASTEXITCODE
    # Cgroup peak includes both the Celery supervisor and prefork child, not
    # merely parser RSS. Read before removing the tested worker container.
    $memoryPeak = & docker exec $workerName cat /sys/fs/cgroup/memory.peak
    if ($LASTEXITCODE -ne 0) { throw "Cannot measure catalog worker aggregate memory peak." }
    $state = & docker inspect --format '{{json .State}}' $workerName | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw "Cannot inspect tested worker state." }
    [ordered]@{ worker_peak_bytes = [long]$memoryPeak; worker_running = $state.Running; worker_oom = $state.OOMKilled } | ConvertTo-Json -Compress
    if ($benchmarkExit -ne 0) {
        # Emit only fixed timeout marker counts; no raw task traceback or payload.
        $workerLog = @(& docker logs --tail 200 $workerName 2>&1)
        if ($LASTEXITCODE -ne 0) { throw "Cannot read failed worker diagnostics." }
        [ordered]@{
            soft_timeout_markers = @($workerLog | Select-String 'Soft time limit').Count
            hard_timeout_markers = @($workerLog | Select-String 'Hard time limit').Count
            sanitized_task_failures = @($workerLog | Select-String 'Catalog file processing failed').Count
        } | ConvertTo-Json -Compress
    }
    if ($benchmarkExit -ne 0 -or -not $state.Running -or $state.OOMKilled) { throw "Catalog write benchmark failed; see numeric case results." }
} finally {
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
    foreach ($path in $envFiles) {
        try { Remove-Item -LiteralPath $path -Force -ErrorAction Stop }
        catch { $cleanupFailed = $true }
    }
    if ($cleanupFailed) { throw "Isolated benchmark cleanup failed; inspect resources with prefix $prefix before continuing." }
}
