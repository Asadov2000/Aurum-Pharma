[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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
    & docker @composeArgs @args
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed with exit code $LASTEXITCODE"
    }
}

function Get-E2EContainerId {
    param([Parameter(Mandatory = $true)][string]$Service)

    $containerIds = & docker @composeArgs ps --quiet $Service
    $exitCode = $LASTEXITCODE
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
    Invoke-E2ECompose up --detach --build postgres redis minio backend celery-worker frontend

    Wait-HttpOk "E2E backend" "http://localhost:18000/healthz"
    Wait-HttpOk "E2E frontend" "http://localhost:15173"

    Invoke-E2ECompose exec -T backend alembic upgrade head
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
    [Environment]::SetEnvironmentVariable("E2E_POSTGRES_DB", "aurum", "Process")
    [Environment]::SetEnvironmentVariable(
        "E2E_REDIS_CONTAINER",
        (Get-E2EContainerId "redis"),
        "Process"
    )

    Push-Location (Join-Path $workspace "frontend")
    $insideFrontend = $true
    & pnpm e2e
    if ($LASTEXITCODE -ne 0) {
        throw "Playwright failed with exit code $LASTEXITCODE"
    }
}
catch {
    $failure = $_
}
finally {
    if ($insideFrontend) {
        Pop-Location
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
