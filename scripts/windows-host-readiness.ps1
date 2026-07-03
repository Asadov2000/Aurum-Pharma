param(
    [switch]$FailOnMissing
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

$present = New-Object System.Collections.Generic.List[string]
$missing = New-Object System.Collections.Generic.List[string]
$uncertain = New-Object System.Collections.Generic.List[string]
$recommended = New-Object System.Collections.Generic.List[string]

function Add-Present {
    param([string]$Message)
    $present.Add($Message) | Out-Null
}

function Add-Missing {
    param([string]$Message)
    $missing.Add($Message) | Out-Null
}

function Add-Uncertain {
    param([string]$Message)
    $uncertain.Add($Message) | Out-Null
}

function Add-Recommended {
    param([string]$Message)
    $recommended.Add($Message) | Out-Null
}

function Write-Section {
    param(
        [string]$Title,
        [System.Collections.Generic.List[string]]$Items
    )

    Write-Host ""
    Write-Host $Title
    Write-Host ("-" * $Title.Length)

    if ($Items.Count -eq 0) {
        Write-Host "none"
        return
    }

    foreach ($item in $Items) {
        Write-Host "- $item"
    }
}

function Test-Command {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-TextCommand {
    param(
        [string]$Exe,
        [string[]]$Arguments
    )

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & $Exe @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = ($output -join "`n").Trim()
    }
}

function Get-VisualStudioInstallations {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vswhere)) {
        return @()
    }

    $result = Invoke-TextCommand -Exe $vswhere -Arguments @(
        "-all",
        "-format",
        "json",
        "-products",
        "*",
        "-requiresAny",
        "Microsoft.VisualStudio.Workload.ManagedDesktop",
        "Microsoft.VisualStudio.Component.Windows10SDK.19041"
    )

    if ($result.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($result.Output)) {
        return @()
    }

    return @($result.Output | ConvertFrom-Json)
}

function Get-WindowsSdkVersions {
    $includeRoot = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\Include"
    if (-not (Test-Path $includeRoot)) {
        return @()
    }

    return @(
        Get-ChildItem -Path $includeRoot -Directory |
            Where-Object { $_.Name -match "^\d+\.\d+\.\d+\.\d+$" } |
            Sort-Object Name -Descending |
            Select-Object -ExpandProperty Name
    )
}

Write-Host "Aurum Pharma Windows host readiness audit"
Write-Host "Safe mode: read-only checks, no installs, no system changes."

$osVersion = [Environment]::OSVersion.Version
if ($osVersion.Build -ge 17763) {
    Add-Present "Windows build $($osVersion.Build) meets the WinUI floor (17763+)."
}
else {
    Add-Missing "Windows build $($osVersion.Build) is below the WinUI floor (17763+)."
}

try {
    $developerMode = Get-ItemPropertyValue `
        -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock" `
        -Name "AllowDevelopmentWithoutDevLicense" `
        -ErrorAction Stop

    if ($developerMode -eq 1) {
        Add-Present "Developer Mode is enabled."
    }
    else {
        Add-Recommended "Developer Mode is disabled; enable it before packaged app deploy/debug."
    }
}
catch {
    Add-Uncertain "Developer Mode state could not be read from registry."
}

if (Test-Command "dotnet") {
    $sdks = Invoke-TextCommand -Exe "dotnet" -Arguments @("--list-sdks")
    $sdkOutput = [string]$sdks.Output
    if ($sdks.ExitCode -eq 0 -and $sdkOutput.Trim().Length -gt 0) {
        Add-Present ".NET SDK installed: $($sdkOutput -replace "`n", "; ")."
    }
    else {
        Add-Missing ".NET SDK command exists, but installed SDKs could not be listed."
    }

    $winuiTemplates = Invoke-TextCommand -Exe "dotnet" -Arguments @("new", "list", "winui")
    $winuiTemplateOutput = [string]$winuiTemplates.Output
    if ($winuiTemplates.ExitCode -eq 0 -and $winuiTemplateOutput -match "winui") {
        Add-Present "WinUI dotnet template is available."
    }
    else {
        Add-Missing "WinUI dotnet template is not available (`dotnet new list winui` returned no match)."
    }
}
else {
    Add-Missing ".NET SDK is not available on PATH."
}

$visualStudio = @(Get-VisualStudioInstallations)
if ($visualStudio.Count -gt 0) {
    foreach ($install in $visualStudio) {
        Add-Present "Visual Studio with desktop/Windows SDK workload: $($install.displayName) $($install.catalog.productDisplayVersion)."
    }
}
else {
    Add-Missing "Visual Studio with Managed Desktop and Windows SDK workload was not found by vswhere."
}

$sdkVersions = @(Get-WindowsSdkVersions)
if ($sdkVersions.Count -gt 0) {
    Add-Present "Windows SDK installed: $($sdkVersions[0])."
}
else {
    Add-Missing "Windows SDK include folder was not found."
}

$msbuild = Get-Command "MSBuild.exe" -ErrorAction SilentlyContinue
if ($null -ne $msbuild) {
    Add-Present "MSBuild is available on PATH: $($msbuild.Source)."
}
else {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $msbuildPath = Invoke-TextCommand -Exe $vswhere -Arguments @(
            "-latest",
            "-requires",
            "Microsoft.Component.MSBuild",
            "-find",
            "MSBuild\**\Bin\MSBuild.exe"
        )

        if ($msbuildPath.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($msbuildPath.Output)) {
            Add-Present "MSBuild is available through Visual Studio: $($msbuildPath.Output.Split("`n")[0])."
        }
        else {
            Add-Missing "MSBuild was not found."
        }
    }
    else {
        Add-Missing "MSBuild was not found and vswhere is unavailable."
    }
}

if (Test-Command "winget") {
    Add-Recommended "WinGet is available for later guided prerequisite installation."
}
else {
    Add-Recommended "Install WinGet before automated WinUI prerequisite setup."
}

Write-Section "present" $present
Write-Section "missing" $missing
Write-Section "uncertain" $uncertain
Write-Section "recommended optional tools" $recommended

if ($FailOnMissing -and $missing.Count -gt 0) {
    Write-Error "Windows host prerequisites are missing."
    exit 1
}

Write-Host ""
if ($missing.Count -eq 0) {
    Write-Host "[OK] Windows host toolchain appears ready."
}
else {
    Write-Host "[WARN] Windows host toolchain is not ready yet. Missing: $($missing.Count)."
}
