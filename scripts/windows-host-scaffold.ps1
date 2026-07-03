param(
    [switch]$Create,
    [switch]$Force,
    [switch]$SkipReadiness,
    [switch]$SkipBuild,
    [switch]$Unpackaged,
    [ValidatePattern("^[A-Za-z][A-Za-z0-9_.-]*$")]
    [string]$ProjectName = "AurumPharma.Desktop",
    [ValidateSet("net10.0", "net9.0", "net8.0")]
    [string]$Framework = "net10.0"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$DesktopRoot = Join-Path $RepoRoot "desktop"
$ProjectPath = Join-Path $DesktopRoot $ProjectName
$ReadinessScript = Join-Path $RepoRoot "scripts\windows-host-readiness.ps1"

function Stop-Scaffold {
    param([string]$Message)
    Write-Error $Message
    exit 1
}

function Invoke-Checked {
    param(
        [string]$Title,
        [string[]]$Command,
        [string]$WorkingDirectory = $RepoRoot
    )

    Write-Host ""
    Write-Host "==> $Title"

    $exe = $Command[0]
    $arguments = @()
    if ($Command.Count -gt 1) {
        $arguments = $Command[1..($Command.Count - 1)]
    }

    Push-Location $WorkingDirectory
    try {
        & $exe @arguments
        if ($LASTEXITCODE -ne 0) {
            Stop-Scaffold "$Title failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

$packaging = if ($Unpackaged) { "unpackaged" } else { "packaged" }

Write-Host "Aurum Pharma Windows host scaffold"
Write-Host "Project: $ProjectName"
Write-Host "Path: $ProjectPath"
Write-Host "Framework: $Framework"
Write-Host "Packaging: $packaging"

if (-not $Create) {
    Write-Host ""
    Write-Host "Dry-run mode: no files created."
    Write-Host "After WinUI prerequisites are ready, create the project with:"
    Write-Host "powershell -ExecutionPolicy Bypass -File .\scripts\windows-host-scaffold.ps1 -Create"
    Write-Host ""
    Write-Host "The script will run readiness checks, call dotnet new winui, then build the generated project."
    exit 0
}

if (-not (Test-Path $ReadinessScript)) {
    Stop-Scaffold "Missing readiness audit script: $ReadinessScript"
}

if ((Test-Path $ProjectPath) -and -not $Force) {
    Stop-Scaffold "Target project path already exists: $ProjectPath. Re-run with -Force only if overwrite is intentional."
}

if (-not $SkipReadiness) {
    Invoke-Checked "Running strict Windows host readiness audit" @(
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $ReadinessScript,
        "-FailOnMissing"
    )
}

if (-not (Test-Path $DesktopRoot)) {
    New-Item -ItemType Directory -Path $DesktopRoot | Out-Null
}

$templateArgs = @("new", "winui", "-o", $ProjectPath, "-f", $Framework)
if ($Unpackaged) {
    $templateArgs += "--unpackaged"
}

if ($Force) {
    $templateArgs += "--force"
}

$createCommand = @("dotnet") + $templateArgs
Invoke-Checked "Creating WinUI 3 project" $createCommand

$projectFile = Join-Path $ProjectPath "$ProjectName.csproj"
if (-not (Test-Path $projectFile)) {
    $fallbackProject = Get-ChildItem -Path $ProjectPath -Filter "*.csproj" | Select-Object -First 1
    if ($null -eq $fallbackProject) {
        Stop-Scaffold "WinUI template completed, but no .csproj file was found in $ProjectPath"
    }
    $projectFile = $fallbackProject.FullName
}

Write-Host ""
Write-Host "Generated project file: $projectFile"

if (-not $SkipBuild) {
    Invoke-Checked "Building generated WinUI project" @(
        "dotnet",
        "build",
        $projectFile,
        "-c",
        "Debug"
    )
}

Write-Host ""
Write-Host "[OK] WinUI host project scaffold completed."
Write-Host "Next step: wire WebView2 startup and the aurumDesktop bridge from docs\desktop-bridge.md."
