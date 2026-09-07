[CmdletBinding()]
param(
    [string]$BackendImage = "aurum-pharma-e2e-backend:local",
    [ValidateSet("all", "acl", "celery", "network")][string]$Mode = "all"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$redisImage = "redis:7-alpine@sha256:02f2cc4882f8bf87c79a220ac958f58c700bdec0dfb9b9ea61b62fb0e8f1bfcf"
$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backend = (Resolve-Path (Join-Path $workspace "backend")).Path
$redisEntrypoint = (Resolve-Path (Join-Path $workspace "infra/redis/start-with-worker-acl.sh")).Path
$suffix = [guid]::NewGuid().ToString("N").Substring(0, 12)
$prefix = "aurum-broker-test-$suffix"
$scratch = Join-Path $workspace ".codex"
[void](New-Item -ItemType Directory -Path $scratch -Force)
$envFile = Join-Path $scratch "$prefix.env"
$networkEnv = Join-Path $scratch "$prefix-network.env"
$missingPasswordEnv = Join-Path $scratch "$prefix-missing-password.env"
$networks = @{}
$containers = [Collections.Generic.List[string]]::new()
$createdNetworks = [Collections.Generic.List[string]]::new()
$probe = "scripts/test_worker_broker_boundaries.py"

function Invoke-BoundaryDocker {
    param([Parameter(Mandatory = $true)][string[]]$Arguments, [switch]$Quiet)
    if ($Quiet) { & docker @Arguments *> $null } else { & docker @Arguments }
    if ($LASTEXITCODE -ne 0) { throw "Worker boundary Docker command failed (exit $LASTEXITCODE)." }
}

function Get-ContainerIp {
    param([string]$Name, [string]$Network)
    $model = (& docker inspect $Name | ConvertFrom-Json)[0]
    if ($LASTEXITCODE -ne 0) { throw "Cannot inspect isolated test container." }
    return $model.NetworkSettings.Networks.$Network.IPAddress
}

function Invoke-Probe {
    param([Parameter(Mandatory = $true)][string]$Mode)
    Invoke-BoundaryDocker -Arguments @(
        "run", "--rm", "--network", $networks.broker,
        "--env-file", $envFile, "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--mount", "type=bind,source=$backend,target=/app,readonly",
        "--workdir", "/app", "--entrypoint", "python", $BackendImage, $probe, $Mode
    )
}

function Assert-StartupRejected {
    param([string]$CaseName, [string[]]$InvalidValues)
    [IO.File]::WriteAllLines($missingPasswordEnv, $InvalidValues, [Text.UTF8Encoding]::new($false))
    $negativeName = "$prefix-$CaseName"
    Invoke-BoundaryDocker -Arguments @(
        "run", "--detach", "--name", $negativeName, "--network", "none",
        "--env-file", $missingPasswordEnv, "--tmpfs", "/data:rw,noexec,nosuid,size=32m",
        "--mount", "type=bind,source=$redisEntrypoint,target=/config/start-with-worker-acl.sh,readonly",
        "--entrypoint", "/bin/sh", $redisImage, "/config/start-with-worker-acl.sh"
    ) -Quiet
    $containers.Add($negativeName)
    for ($attempt = 0; $attempt -lt 5; $attempt++) {
        $state = ((& docker inspect $negativeName | ConvertFrom-Json)[0]).State
        if ($LASTEXITCODE -ne 0) { throw "Cannot inspect invalid-password startup test." }
        if (-not $state.Running) { break }
        Start-Sleep -Seconds 1
    }
    if ($state.Running -or $state.ExitCode -ne 1) {
        throw "Redis must refuse unsafe password configuration: $CaseName."
    }
    Write-Host "Redis $CaseName configuration: startup refused."
}

try {
    $values = @(
        "REDIS_PASSWORD=$([guid]::NewGuid().ToString('N'))",
        "REDIS_TLS=false", "REDIS_APPENDONLY=no", "BROKER_TEST_HOST=$prefix-redis"
    )
    foreach ($role in @("API", "BEAT", "SYSTEM", "CATALOG", "BILLING", "MAILER")) {
        $values += "REDIS_CELERY_${role}_PASSWORD=$([guid]::NewGuid().ToString('N'))"
    }
    [IO.File]::WriteAllLines($envFile, $values, [Text.UTF8Encoding]::new($false))
    if ($Mode -in @("all", "acl")) {
        Assert-StartupRejected -CaseName "missing-password" -InvalidValues @(
            $values | Where-Object { -not $_.StartsWith("REDIS_PASSWORD=") }
        )
        foreach ($source in @("REDIS_PASSWORD", "REDIS_CELERY_API_PASSWORD")) {
            $replacement = ($values | Where-Object { $_.StartsWith("$source=") }) -replace "^[^=]+=", "REDIS_CELERY_CATALOG_PASSWORD="
            $case = if ($source -eq "REDIS_PASSWORD") { "cache-password-reuse" } else { "role-password-reuse" }
            Assert-StartupRejected -CaseName $case -InvalidValues @(
                $values | ForEach-Object {
                    if ($_.StartsWith("REDIS_CELERY_CATALOG_PASSWORD=")) { $replacement } else { $_ }
                }
            )
        }
    }
    foreach ($zone in @("db", "broker", "storage")) {
        $networks[$zone] = "$prefix-$zone"
        Invoke-BoundaryDocker -Arguments @("network", "create", "--internal", $networks[$zone]) -Quiet
        $createdNetworks.Add($networks[$zone])
    }
    $redisName = "$prefix-redis"
    Invoke-BoundaryDocker -Arguments @(
        "run", "--detach", "--name", $redisName,
        "--network", $networks.broker, "--network-alias", "broker-probe",
        "--env-file", $envFile, "--tmpfs", "/data:rw,noexec,nosuid,size=32m",
        "--mount", "type=bind,source=$redisEntrypoint,target=/config/start-with-worker-acl.sh,readonly",
        "--entrypoint", "/bin/sh", $redisImage, "/config/start-with-worker-acl.sh"
    ) -Quiet
    $containers.Add($redisName)
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        & docker exec $redisName /bin/sh -ec 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli ping' *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw "Isolated Redis did not become ready." }
    if ($Mode -in @("all", "acl")) { Invoke-Probe -Mode "acl" }
    if ($Mode -in @("all", "celery")) { Invoke-Probe -Mode "celery" }
    if ($Mode -notin @("all", "network")) { return }

    $targets = @{ broker = @("broker-probe", (Get-ContainerIp $redisName $networks.broker), 6379) }
    foreach ($listener in @(@("db", 5432), @("storage", 9000))) {
        $zone = [string]$listener[0]
        $port = [string]$listener[1]
        $name = "$prefix-$zone-listener"
        Invoke-BoundaryDocker -Arguments @(
            "run", "--detach", "--name", $name, "--network", $networks[$zone],
            "--network-alias", "$zone-probe", "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--mount", "type=bind,source=$backend,target=/app,readonly",
            "--workdir", "/app", "--entrypoint", "python", $BackendImage,
            $probe, "listener", "--port", $port
        ) -Quiet
        $containers.Add($name)
        $targets[$zone] = @("$zone-probe", (Get-ContainerIp $name $networks[$zone]), [int]$port)
    }
    [IO.File]::WriteAllLines(
        $networkEnv,
        @("BOUNDARY_NETWORK_TARGETS=$($targets | ConvertTo-Json -Compress)", "PYTHONDONTWRITEBYTECODE=1"),
        [Text.UTF8Encoding]::new($false)
    )
    $roleZones = @{
        api = @("broker", "db", "storage"); beat = @("broker")
        system = @("broker", "db"); catalog = @("broker", "db", "storage")
        billing = @("broker", "db"); mailer = @("broker", "db")
    }
    foreach ($role in @("api", "beat", "system", "catalog", "billing", "mailer")) {
        $name = "$prefix-$role-network"
        Invoke-BoundaryDocker -Arguments @(
            "create", "--name", $name, "--network", $networks.broker,
            "--env-file", $networkEnv,
            "--mount", "type=bind,source=$backend,target=/app,readonly",
            "--workdir", "/app", "--entrypoint", "python", $BackendImage,
            $probe, "network", "--role", $role
        ) -Quiet
        $containers.Add($name)
        foreach ($zone in $roleZones[$role] | Select-Object -Skip 1) {
            Invoke-BoundaryDocker -Arguments @("network", "connect", $networks[$zone], $name) -Quiet
        }
        Invoke-BoundaryDocker -Arguments @("start", "--attach", $name)
        $exitCode = (& docker inspect --format "{{.State.ExitCode}}" $name).Trim()
        if ($LASTEXITCODE -ne 0 -or $exitCode -ne "0") {
            throw "Role network probe failed for $role."
        }
    }
    Write-Host "Worker boundary probes completed (mode: $Mode)."
} finally {
    foreach ($name in $containers) { & docker rm -f $name *> $null }
    foreach ($name in $createdNetworks) { & docker network rm $name *> $null }
    foreach ($path in @($envFile, $networkEnv, $missingPasswordEnv)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
    }
}
