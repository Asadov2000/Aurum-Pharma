[CmdletBinding(PositionalBinding = $false)]
param(
    [ValidateSet("Auto", "Quick", "Full")]
    [string]$Mode = "Auto",
    [string]$BaseRef = "origin/main",
    [string[]]$ChangedPath = @(),
    [switch]$PlanOnly,
    [ValidateSet("Text", "Json")]
    [string]$Format = "Text"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$scopeScript = Join-Path $PSScriptRoot "task-scope.ps1"
$selfTestScript = Join-Path $PSScriptRoot "test-verification-tools.ps1"
$untrackedWhitespaceScript = Join-Path $PSScriptRoot "check-untracked-whitespace.ps1"
$powershellExecutable = (Get-Process -Id $PID).Path

if ($Format -eq "Json" -and -not $PlanOnly) {
    throw "-Format Json is supported only with -PlanOnly."
}
if ($ChangedPath.Count -gt 0 -and -not $PlanOnly) {
    throw "-ChangedPath is a planning/test input and requires -PlanOnly."
}

function New-Step {
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    return [pscustomobject][ordered]@{
        id = $Id
        name = $Name
        cwd = $WorkingDirectory
        command = $Command
        arguments = @($Arguments)
    }
}

function Add-Step {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.Generic.List[object]]$Steps,
        [Parameter(Mandatory = $true)][object]$Step
    )

    $Steps.Add($Step)
}

function Format-CommandArgument {
    param([Parameter(Mandatory = $true)][string]$Value)

    if ($Value -match '[\s"'']') {
        return '"' + $Value.Replace('"', '\"') + '"'
    }
    return $Value
}

function Invoke-LoggedStep {
    param(
        [Parameter(Mandatory = $true)][object]$Step,
        [Parameter(Mandatory = $true)][string]$LogDirectory,
        [Parameter(Mandatory = $true)][int]$Number,
        [Parameter(Mandatory = $true)][int]$Total
    )

    $safeId = $Step.id -replace '[^a-zA-Z0-9_-]', '-'
    $logPath = Join-Path $LogDirectory ("{0:D2}-{1}.log" -f $Number, $safeId)
    $timer = [Diagnostics.Stopwatch]::StartNew()
    Write-Host ("[{0}/{1}] {2}" -f $Number, $Total, $Step.name)

    Push-Location $Step.cwd
    try {
        $arguments = @($Step.arguments)
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $Step.command @arguments *> $logPath
            $exitCode = $LASTEXITCODE
            if ($null -eq $exitCode) {
                $exitCode = 0
            }
        }
        catch {
            $_ | Out-File -LiteralPath $logPath -Append -Encoding utf8
            $exitCode = 1
        }
        finally {
            $ErrorActionPreference = $previousPreference
        }
    }
    finally {
        Pop-Location
        $timer.Stop()
    }

    if ($exitCode -ne 0) {
        Write-Host ("[FAIL] {0} ({1:n1}s)" -f $Step.name, $timer.Elapsed.TotalSeconds) -ForegroundColor Red
        throw "Verification failed. Detailed log: $logPath"
    }
    Write-Host ("[OK]   {0} ({1:n1}s)" -f $Step.name, $timer.Elapsed.TotalSeconds) -ForegroundColor Green
}

$scopeParameters = @{
    BaseRef = $BaseRef
    Format = "Json"
}
if ($PSBoundParameters.ContainsKey("ChangedPath")) {
    $scopeParameters["ChangedPath"] = @($ChangedPath)
}
$scopeJson = & $scopeScript @scopeParameters
$scope = ($scopeJson -join [Environment]::NewLine) | ConvertFrom-Json

$backendTests = "$($scope.verification.backendTests)"
$frontendTests = "$($scope.verification.frontendTests)"
$e2eMode = "$($scope.verification.e2e)"
$backendQuality = [bool]$scope.verification.backendQuality
$frontendQuality = [bool]$scope.verification.frontendQuality
$frontendBuild = [bool]$scope.verification.frontendBuild
$toolingSelfTest = [bool]$scope.verification.toolingSelfTest

if ($Mode -eq "Quick" -and -not [bool]$scope.flags.critical) {
    $e2eMode = "none"
    if ($scope.risk -ne "full") {
        $frontendBuild = $false
    }
}
elseif ($Mode -eq "Full") {
    $backendTests = "full"
    $frontendTests = "full"
    $e2eMode = "full"
    $backendQuality = $true
    $frontendQuality = $true
    $frontendBuild = $true
    $toolingSelfTest = $true
}

$steps = [System.Collections.Generic.List[object]]::new()
Add-Step -Steps $steps -Step (New-Step -Id "diff-committed" -Name "Git whitespace: committed diff" -WorkingDirectory $repoRoot -Command "git" -Arguments @("diff", "--check", "$BaseRef...HEAD", "--"))
Add-Step -Steps $steps -Step (New-Step -Id "diff-staged" -Name "Git whitespace: staged diff" -WorkingDirectory $repoRoot -Command "git" -Arguments @("diff", "--cached", "--check", "--"))
Add-Step -Steps $steps -Step (New-Step -Id "diff-working" -Name "Git whitespace: working diff" -WorkingDirectory $repoRoot -Command "git" -Arguments @("diff", "--check", "--"))
$untrackedArguments = @("-NoProfile", "-File", $untrackedWhitespaceScript)
if ($env:OS -eq "Windows_NT") {
    $untrackedArguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $untrackedWhitespaceScript)
}
Add-Step -Steps $steps -Step (New-Step -Id "diff-untracked" -Name "Git whitespace: untracked files" -WorkingDirectory $repoRoot -Command $powershellExecutable -Arguments $untrackedArguments)

if ($toolingSelfTest) {
    $selfTestArguments = @("-NoProfile", "-File", $selfTestScript)
    if ($env:OS -eq "Windows_NT") {
        $selfTestArguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $selfTestScript)
    }
    Add-Step -Steps $steps -Step (New-Step -Id "verification-self-test" -Name "Verification tooling self-test" -WorkingDirectory $repoRoot -Command $powershellExecutable -Arguments $selfTestArguments)
}

if ($backendTests -ne "none") {
    $pytestTargets = @("tests")
    if ($backendTests -eq "targeted" -and @($scope.targets.backend).Count -gt 0) {
        $pytestTargets = @($scope.targets.backend | ForEach-Object { "$_" })
    }
    Add-Step -Steps $steps -Step (New-Step -Id "backend-tests" -Name "Backend tests ($backendTests)" -WorkingDirectory (Join-Path $repoRoot "backend") -Command "poetry" -Arguments (@("run", "pytest") + $pytestTargets + @("-q", "--tb=short")))
}
if ($backendQuality) {
    Add-Step -Steps $steps -Step (New-Step -Id "backend-ruff" -Name "Backend Ruff" -WorkingDirectory (Join-Path $repoRoot "backend") -Command "poetry" -Arguments @("run", "ruff", "check", "app", "tests"))
    Add-Step -Steps $steps -Step (New-Step -Id "backend-black" -Name "Backend Black" -WorkingDirectory (Join-Path $repoRoot "backend") -Command "poetry" -Arguments @("run", "black", "--check", "app", "tests"))
    Add-Step -Steps $steps -Step (New-Step -Id "backend-mypy" -Name "Backend Mypy" -WorkingDirectory (Join-Path $repoRoot "backend") -Command "poetry" -Arguments @("run", "mypy", "app"))
}

if ($frontendTests -ne "none") {
    $vitestTargets = @("tests")
    if ($frontendTests -eq "targeted" -and @($scope.targets.frontend).Count -gt 0) {
        $vitestTargets = @($scope.targets.frontend | ForEach-Object { "$_" })
    }
    Add-Step -Steps $steps -Step (New-Step -Id "frontend-tests" -Name "Frontend tests ($frontendTests)" -WorkingDirectory (Join-Path $repoRoot "frontend") -Command "pnpm" -Arguments (@("exec", "vitest", "run") + $vitestTargets + @("--reporter=dot")))
}
if ($frontendQuality) {
    Add-Step -Steps $steps -Step (New-Step -Id "frontend-typecheck" -Name "Frontend typecheck" -WorkingDirectory (Join-Path $repoRoot "frontend") -Command "pnpm" -Arguments @("typecheck"))
    Add-Step -Steps $steps -Step (New-Step -Id "frontend-lint" -Name "Frontend lint" -WorkingDirectory (Join-Path $repoRoot "frontend") -Command "pnpm" -Arguments @("lint"))
}
if ($frontendBuild) {
    Add-Step -Steps $steps -Step (New-Step -Id "frontend-build" -Name "Frontend production build" -WorkingDirectory (Join-Path $repoRoot "frontend") -Command "pnpm" -Arguments @("build"))
}

if ($e2eMode -ne "none") {
    $e2eArguments = @("-NoProfile", "-File", (Join-Path $PSScriptRoot "e2e-isolated.ps1"))
    if ($env:OS -eq "Windows_NT") {
        $e2eArguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "e2e-isolated.ps1"))
    }
    if ($e2eMode -eq "targeted") {
        $e2eArguments += @($scope.targets.e2e | ForEach-Object { "$_" })
    }
    Add-Step -Steps $steps -Step (New-Step -Id "e2e" -Name "Isolated Playwright E2E ($e2eMode)" -WorkingDirectory $repoRoot -Command $powershellExecutable -Arguments $e2eArguments)
}

$plan = [ordered]@{
    version = 1
    mode = $Mode.ToLowerInvariant()
    baseRef = $BaseRef
    warning = if ($Mode -eq "Quick") { "Quick is for development feedback only; the GitHub CI gate remains mandatory." } else { $null }
    scope = $scope
    steps = @($steps)
}

if ($PlanOnly) {
    if ($Format -eq "Json") {
        $plan | ConvertTo-Json -Depth 8
        return
    }

    Write-Host "Aurum verification plan"
    Write-Host "  Mode:  $($Mode.ToLowerInvariant())"
    Write-Host "  Risk:  $($scope.risk)"
    Write-Host "  Paths: $(@($scope.paths).Count)"
    if ($Mode -eq "Quick") {
        Write-Host "  Note:  Quick does not replace the required GitHub CI gate." -ForegroundColor Yellow
    }
    for ($index = 0; $index -lt $steps.Count; $index++) {
        $step = $steps[$index]
        $displayArguments = @($step.arguments | ForEach-Object { Format-CommandArgument -Value "$_" })
        Write-Host ("  {0}. {1}" -f ($index + 1), $step.name)
        Write-Host ("     {0} {1}" -f $step.command, ($displayArguments -join " "))
    }
    return
}

Write-Host "Aurum verification"
Write-Host "  Mode: $($Mode.ToLowerInvariant()) | Risk: $($scope.risk) | Changed paths: $(@($scope.paths).Count)"
if ($Mode -eq "Quick") {
    Write-Host "  Quick is not a merge/release gate; GitHub CI is still required." -ForegroundColor Yellow
}

$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$logDirectory = Join-Path $tempRoot ("aurum-verify-" + [Guid]::NewGuid().ToString("N"))
[IO.Directory]::CreateDirectory($logDirectory) | Out-Null
$completed = $false
try {
    for ($index = 0; $index -lt $steps.Count; $index++) {
        Invoke-LoggedStep -Step $steps[$index] -LogDirectory $logDirectory -Number ($index + 1) -Total $steps.Count
    }
    $completed = $true
}
finally {
    if ($completed) {
        $resolvedLogDirectory = [IO.Path]::GetFullPath($logDirectory)
        if (-not $resolvedLogDirectory.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a verification log directory outside the system temp directory."
        }
        [IO.Directory]::Delete($resolvedLogDirectory, $true)
    }
}

Write-Host ("Verification passed: {0} steps." -f $steps.Count) -ForegroundColor Green
