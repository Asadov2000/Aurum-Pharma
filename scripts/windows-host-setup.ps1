param(
    [switch]$Apply,
    [switch]$SkipPreflight,
    [switch]$SkipPostCheck
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$ConfigPath = Join-Path $RepoRoot "infra\windows\winui-prerequisites.yaml"
$ReadinessScript = Join-Path $RepoRoot "scripts\windows-host-readiness.ps1"

function Stop-Setup {
    param([string]$Message)
    Write-Error $Message
    exit 1
}

function Test-Command {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-Checked {
    param(
        [string]$Title,
        [string[]]$Command
    )

    Write-Host ""
    Write-Host "==> $Title"

    $exe = $Command[0]
    $arguments = @()
    if ($Command.Count -gt 1) {
        $arguments = $Command[1..($Command.Count - 1)]
    }

    & $exe @arguments
    if ($LASTEXITCODE -ne 0) {
        Stop-Setup "$Title failed with exit code $LASTEXITCODE"
    }
}

if (-not (Test-Path $ConfigPath)) {
    Stop-Setup "Missing WinUI prerequisites config: $ConfigPath"
}

if (-not (Test-Path $ReadinessScript)) {
    Stop-Setup "Missing readiness audit script: $ReadinessScript"
}

Write-Host "Aurum Pharma Windows host setup"
Write-Host "Config: $ConfigPath"

if (-not $Apply) {
    Write-Host ""
    Write-Host "Dry-run mode: no installs, no system changes."
    Write-Host "This setup would run:"
    Write-Host "winget configure -f winui-prerequisites.yaml --accept-configuration-agreements --disable-interactivity"
    Write-Host ""
    Write-Host "To apply prerequisites intentionally, run:"
    Write-Host "powershell -ExecutionPolicy Bypass -File .\scripts\windows-host-setup.ps1 -Apply"
    Write-Host ""
    Write-Host "Current readiness:"
    & powershell -ExecutionPolicy Bypass -File $ReadinessScript
    exit $LASTEXITCODE
}

if (-not (Test-Command "winget")) {
    Stop-Setup "WinGet is not available. Install or repair App Installer first."
}

if (-not $SkipPreflight) {
    Invoke-Checked "Running preflight readiness audit" @(
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $ReadinessScript
    )
}

Write-Host ""
Write-Host "Applying WinUI prerequisites. This can install Visual Studio components and enable Developer Mode."

Push-Location (Split-Path $ConfigPath)
try {
    Invoke-Checked "Applying WinGet configuration" @(
        "winget",
        "configure",
        "-f",
        (Split-Path -Leaf $ConfigPath),
        "--accept-configuration-agreements",
        "--disable-interactivity"
    )
}
finally {
    Pop-Location
}

if (-not $SkipPostCheck) {
    Invoke-Checked "Running strict readiness audit after setup" @(
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $ReadinessScript,
        "-FailOnMissing"
    )
}

Write-Host ""
Write-Host "[OK] Windows host setup completed."
