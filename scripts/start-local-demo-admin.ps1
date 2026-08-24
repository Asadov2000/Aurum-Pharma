[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$SkipBuild,
    [switch]$Rebuild,
    [switch]$SkipSeed,
    [switch]$NoBrowser,
    [switch]$PauseOnError
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$showcaseLauncher = Join-Path $PSScriptRoot "start-showcase-demo.ps1"
$failed = $false

function Test-DockerReady {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & docker version --format "{{.Server.Version}}" *> $null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Start-DockerDesktopIfNeeded {
    if (Test-DockerReady) {
        return
    }

    $dockerDesktopCandidates = @(
        (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"),
        (Join-Path $env:LOCALAPPDATA "Docker\Docker Desktop.exe")
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    $dockerDesktop = $dockerDesktopCandidates | Where-Object {
        Test-Path -LiteralPath $_ -PathType Leaf
    } | Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($dockerDesktop)) {
        throw "Docker is unavailable. Install or start Docker Desktop and try again."
    }

    Write-Host "Docker Desktop is not ready. Starting it..."
    Start-Process -FilePath $dockerDesktop -WindowStyle Hidden | Out-Null
    for ($attempt = 1; $attempt -le 60; $attempt++) {
        Start-Sleep -Seconds 2
        if (Test-DockerReady) {
            Write-Host "Docker Desktop is ready."
            return
        }
    }

    throw "Docker Desktop did not become ready within two minutes."
}

function Test-ShowcaseImagesAvailable {
    $requiredImages = @(
        "aurum-pharma-demo-backend:local",
        "aurum-pharma-demo-frontend:local"
    )
    foreach ($image in $requiredImages) {
        & docker image inspect $image *> $null
        if ($LASTEXITCODE -ne 0) {
            return $false
        }
    }
    return $true
}

try {
    Set-Location $projectRoot
    Write-Host "Aurum Pharma safe local launcher"
    Write-Host "Project: $projectRoot"

    if (-not (Test-Path -LiteralPath $showcaseLauncher -PathType Leaf)) {
        throw "Showcase launcher not found: $showcaseLauncher"
    }

    if (-not $DryRun) {
        Start-DockerDesktopIfNeeded
    }

    if ($SkipBuild -and $Rebuild) {
        throw "Use either -SkipBuild or -Rebuild, not both."
    }

    $showcaseArguments = @{}
    if ($DryRun) { $showcaseArguments["DryRun"] = $true }
    $useCachedImages = $SkipBuild
    if (-not $DryRun -and -not $Rebuild -and (Test-ShowcaseImagesAvailable)) {
        $useCachedImages = $true
        Write-Host "Using local application images for a fast, offline-friendly start."
        Write-Host "Use -Rebuild after application code changes."
    }
    if ($useCachedImages) { $showcaseArguments["SkipBuild"] = $true }
    if ($SkipSeed) { $showcaseArguments["SkipSeed"] = $true }
    & $showcaseLauncher @showcaseArguments

    Write-Host ""
    Write-Host "Aurum Pharma is ready."
    Write-Host "Frontend: http://localhost:5173"
    Write-Host "API docs: http://localhost:8000/docs"

    if (-not $DryRun -and -not $NoBrowser) {
        Start-Process "http://localhost:5173" | Out-Null
    }
} catch {
    $failed = $true
    Write-Host ""
    Write-Host "FAILED:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
} finally {
    if ($failed -and $PauseOnError -and -not $DryRun) {
        Write-Host ""
        Read-Host "Press Enter to close this window"
    }
}

if ($failed) {
    exit 1
}

exit 0
