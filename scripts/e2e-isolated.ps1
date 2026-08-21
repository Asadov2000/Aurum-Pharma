[CmdletBinding()]
param(
    [switch]$PlanOnly,
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$PlaywrightArgs = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($PlanOnly) {
    Write-Output (ConvertTo-Json -InputObject @($PlaywrightArgs) -Compress)
    return
}

$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$composeFile = Join-Path $workspace "docker-compose.e2e.yml"
$projectName = "aurum-e2e-local"
$composeArgs = @(
    "compose",
    "--project-name", $projectName,
    "--file", $composeFile
)
$environmentNames = @(
    "E2E_API_URL",
    "E2E_BASE_URL",
    "E2E_POSTGRES_CONTAINER",
    "E2E_POSTGRES_DB",
    "E2E_REDIS_CONTAINER"
)
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

function Invoke-E2ECompose {
    $previousPreference = $ErrorActionPreference
    $exitCode = 1
    $ErrorActionPreference = "Continue"
    try {
        & docker @composeArgs @args
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        throw "docker compose failed with exit code $exitCode"
    }
}

function Get-E2EContainerId {
    param([Parameter(Mandatory = $true)][string]$Service)

    $previousPreference = $ErrorActionPreference
    $containerIds = @()
    $exitCode = 1
    $ErrorActionPreference = "Continue"
    try {
        $containerIds = & docker @composeArgs ps --quiet $Service
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    $containerId = @($containerIds)[0]
    if ($exitCode -ne 0 -or [string]::IsNullOrWhiteSpace($containerId)) {
        throw "Unable to resolve the disposable $Service container"
    }
    return $containerId.Trim()
}

function Wait-HttpOk {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Url
    )

    for ($attempt = 1; $attempt -le 90; $attempt++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                Write-Host "$Name is ready"
                return
            }
        }
        catch {
            if ($attempt -eq 90) {
                throw "$Name did not become ready: $Url"
            }
        }
        Start-Sleep -Seconds 2
    }
}

$failure = $null
$insideFrontend = $false
try {
    & docker version *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Desktop is not available"
    }

    Write-Host "Resetting disposable E2E stack..."
    Invoke-E2ECompose down --volumes --remove-orphans
    Invoke-E2ECompose up --detach --build postgres redis minio
    Invoke-E2ECompose run --rm db-role-bootstrap
    Invoke-E2ECompose run --rm --build migrate
    Invoke-E2ECompose up --detach --build backend celery-worker billing-worker platform-mailer frontend

    Wait-HttpOk "E2E backend" "http://localhost:18000/healthz"
    Wait-HttpOk "E2E frontend" "http://localhost:15173"

    Invoke-E2ECompose exec -T -e AURUM_E2E_SEED=1 backend python -m app.seed_e2e
    Invoke-E2ECompose exec -T backend python -m app.seed_demo
    Invoke-E2ECompose restart frontend
    Wait-HttpOk "E2E frontend after restart" "http://localhost:15173"

    [Environment]::SetEnvironmentVariable(
        "E2E_API_URL",
        "http://localhost:18000/api/v1",
        "Process"
    )
    [Environment]::SetEnvironmentVariable("E2E_BASE_URL", "http://localhost:15173", "Process")
    [Environment]::SetEnvironmentVariable(
        "E2E_POSTGRES_CONTAINER",
        (Get-E2EContainerId "postgres"),
        "Process"
    )
    $e2eDatabase = [Environment]::GetEnvironmentVariable("AURUM_E2E_DATABASE", "Process")
    if ([string]::IsNullOrWhiteSpace($e2eDatabase)) {
        $e2eDatabase = "aurum"
    }
    [Environment]::SetEnvironmentVariable("E2E_POSTGRES_DB", $e2eDatabase, "Process")
    [Environment]::SetEnvironmentVariable(
        "E2E_REDIS_CONTAINER",
        (Get-E2EContainerId "redis"),
        "Process"
    )

    Push-Location (Join-Path $workspace "frontend")
    $insideFrontend = $true
    $previousPreference = $ErrorActionPreference
    $exitCode = 1
    $ErrorActionPreference = "Continue"
    try {
        if ($PlaywrightArgs.Count -gt 0) {
            & pnpm e2e -- @PlaywrightArgs
        }
        else {
            & pnpm e2e
        }
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        throw "Playwright failed with exit code $exitCode"
    }
}
catch {
    $failure = $_
}
finally {
    if ($insideFrontend) {
        Pop-Location
    }

    if ($null -ne $failure) {
        Write-Host "E2E failed; collecting service logs before cleanup..."
        try {
            & docker @composeArgs logs --no-color --tail 200 backend billing-worker frontend
        }
        catch {
            Write-Warning "Unable to collect E2E service logs: $($_.Exception.Message)"
        }
    }

    Write-Host "Removing disposable E2E containers and volumes..."
    try {
        Invoke-E2ECompose down --volumes --remove-orphans
    }
    catch {
        if ($null -eq $failure) {
            $failure = $_
        }
        else {
            Write-Warning "E2E cleanup also failed: $($_.Exception.Message)"
        }
    }

    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], "Process")
    }
}

if ($null -ne $failure) {
    throw $failure
}
