[CmdletBinding(PositionalBinding = $false)]
param(
    [ValidateSet("Preflight", "Auto", "Quick", "Full")]
    [string]$Mode = "Auto",
    [string]$BaseRef = "origin/main",
    [ValidateSet("Docker", "Poetry")]
    [string]$BackendRuntime = "Docker",
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
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [hashtable]$Environment = @{}
    )

    return [pscustomobject][ordered]@{
        id = $Id
        name = $Name
        cwd = $WorkingDirectory
        command = $Command
        arguments = @($Arguments)
        environment = $Environment
    }
}

function Add-Step {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.Generic.List[object]]$Steps,
        [Parameter(Mandatory = $true)][object]$Step
    )

    $Steps.Add($Step)
}

function New-BackendStep {
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Check,
        [string[]]$Arguments = @()
    )

    $backendDirectory = Join-Path $repoRoot "backend"
    if ($BackendRuntime -eq "Poetry") {
        $toolArguments = switch ($Check) {
            "Tests" { @("run", "pytest") + $Arguments }
            "Ruff" { @("run", "ruff", "check", "app", "tests") }
            "Black" { @("run", "black", "--check", "app", "tests") }
            "Mypy" { @("run", "mypy", "app") }
        }
        return New-Step -Id $Id -Name $Name -WorkingDirectory $backendDirectory -Command "poetry" -Arguments $toolArguments
    }

    $runnerArguments = @("-NoProfile")
    if ($env:OS -eq "Windows_NT") {
        $runnerArguments += @("-ExecutionPolicy", "Bypass")
    }
    $runnerArguments += @("-File", (Join-Path $PSScriptRoot "backend-isolated.ps1"), "-Check", $Check)
    $runnerArguments += $Arguments
    return New-Step -Id $Id -Name $Name -WorkingDirectory $backendDirectory -Command $powershellExecutable -Arguments $runnerArguments
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

    $previousEnvironment = @{}
    $previousExitVariable = Get-Variable -Name LASTEXITCODE -Scope Global -ErrorAction SilentlyContinue
    $previousExitCode = if ($null -ne $previousExitVariable) { $previousExitVariable.Value } else { $null }
    $exitCode = 1
    $reason = "command_error"
    Push-Location $Step.cwd
    try {
        foreach ($entry in $Step.environment.GetEnumerator()) {
            $previousEnvironment[$entry.Key] = [Environment]::GetEnvironmentVariable($entry.Key, "Process")
            [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
        }
        $arguments = @($Step.arguments)
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = "Stop"
        try {
            $global:LASTEXITCODE = $null
            $resolvedCommand = Get-Command -Name $Step.command -ErrorAction Stop
            # Windows PowerShell wraps native stderr as ErrorRecord even when
            # the process exits successfully. Native exit codes decide success;
            # PowerShell commands still stop on their first error.
            if ($resolvedCommand.CommandType -eq "Application") {
                $ErrorActionPreference = "Continue"
            }
            & $Step.command @arguments *> $logPath
            $commandSucceeded = $?
            $exitCode = $global:LASTEXITCODE
            if ($null -eq $exitCode) {
                $exitCode = if ($commandSucceeded) { 0 } else { 1 }
            }
            $reason = if ($exitCode -eq 0) { $null } else { "exit_code" }
        }
        catch {
            $_ | Out-File -LiteralPath $logPath -Append -Encoding utf8
            if ($null -ne $global:LASTEXITCODE -and $global:LASTEXITCODE -ne 0) {
                $exitCode = $global:LASTEXITCODE
                $reason = "exit_code"
            }
            if ($_.Exception -is [Management.Automation.PipelineStoppedException] -or $_.Exception -is [OperationCanceledException]) {
                $reason = "interrupted"
            }
        }
        finally {
            $ErrorActionPreference = $previousPreference
        }
    }
    finally {
        foreach ($entry in $previousEnvironment.GetEnumerator()) {
            [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
        }
        Pop-Location
        if ($null -ne $previousExitVariable) {
            $global:LASTEXITCODE = $previousExitCode
        }
        else {
            Remove-Variable -Name LASTEXITCODE -Scope Global -ErrorAction SilentlyContinue
        }
        $timer.Stop()
    }

    $result = [pscustomobject][ordered]@{
        id = $Step.id
        name = $Step.name
        status = if ($exitCode -eq 0) { "passed" } elseif ($reason -eq "interrupted") { "interrupted" } else { "failed" }
        seconds = [Math]::Round($timer.Elapsed.TotalSeconds, 2)
        exitCode = $exitCode
        reason = $reason
        log = $logPath
    }
    if ($exitCode -ne 0) {
        Write-Host ("[FAIL] {0} ({1:n1}s)" -f $Step.name, $timer.Elapsed.TotalSeconds) -ForegroundColor Red
        $failure = New-Object System.Exception("Verification failed. Detailed log: $logPath")
        $failure.Data["StepResult"] = $result
        throw $failure
    }
    Write-Host ("[OK]   {0} ({1:n1}s)" -f $Step.name, $timer.Elapsed.TotalSeconds) -ForegroundColor Green
    return $result
}

function Save-VerificationSummary {
    param(
        [Parameter(Mandatory = $true)][object]$Summary,
        [Parameter(Mandatory = $true)][string]$LogDirectory
    )

    # Only runner-owned metadata is public here; command output and environment
    # values stay in the individual, ignored logs.
    $json = $Summary | ConvertTo-Json -Depth 6
    $lines = @("# Aurum verification", "", "Status: $($Summary.status) | Mode: $($Summary.mode) | Risk: $($Summary.risk)", "")
    if ($Summary.mode -eq "preflight") {
        $lines += @("Preflight omits application tests and builds; Auto/Full and the GitHub CI gate remain mandatory.", "")
    }
    foreach ($item in $Summary.steps) {
        $lines += "- $($item.id): $($item.status); seconds=$($item.seconds); exit=$($item.exitCode); reason=$($item.reason)"
    }
    $encoding = New-Object Text.UTF8Encoding($false)
    foreach ($file in @(
        @{ name = "summary.json"; text = $json },
        @{ name = "summary.md"; text = ($lines -join [Environment]::NewLine) }
    )) {
        $destination = Join-Path $LogDirectory $file.name
        $temporary = "$destination.$([Guid]::NewGuid().ToString('N')).tmp"
        try {
            [IO.File]::WriteAllText($temporary, $file.text, $encoding)
            Move-Item -LiteralPath $temporary -Destination $destination -Force
        }
        finally {
            if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
        }
    }
}

function Invoke-VerificationSteps {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Steps,
        [Parameter(Mandatory = $true)][string]$LogDirectory,
        [Parameter(Mandatory = $true)][string]$Mode,
        [Parameter(Mandatory = $true)][string]$Risk,
        [string]$Revision = "",
        [int]$ChangedPathCount = 0
    )

    $summary = [pscustomobject][ordered]@{
        version = 1
        mode = $Mode.ToLowerInvariant()
        applicationTestsOmitted = ($Mode -eq "Preflight")
        risk = $Risk
        revision = $Revision
        changedPathCount = $ChangedPathCount
        status = "running"
        startedAt = [DateTime]::UtcNow.ToString("o")
        finishedAt = $null
        steps = @($Steps | ForEach-Object {
            [pscustomobject][ordered]@{
                id = $_.id; name = $_.name; status = "not_run"
                seconds = 0; exitCode = $null; reason = $null; log = $null
            }
        })
    }
    Save-VerificationSummary -Summary $summary -LogDirectory $LogDirectory
    try {
        for ($index = 0; $index -lt $Steps.Count; $index++) {
            $summary.steps[$index].status = "running"
            Save-VerificationSummary -Summary $summary -LogDirectory $LogDirectory
            try {
                $summary.steps[$index] = Invoke-LoggedStep -Step $Steps[$index] -LogDirectory $LogDirectory -Number ($index + 1) -Total $Steps.Count
            }
            catch {
                if ($_.Exception.Data.Contains("StepResult")) {
                    $summary.steps[$index] = $_.Exception.Data["StepResult"]
                }
                else {
                    $summary.steps[$index].status = "failed"
                    $summary.steps[$index].reason = "command_error"
                }
                $summary.status = $summary.steps[$index].status
                throw "Verification stopped. Read $(Join-Path $LogDirectory 'summary.md')"
            }
            Save-VerificationSummary -Summary $summary -LogDirectory $LogDirectory
        }
        $summary.status = "passed"
    }
    finally {
        if ($summary.status -eq "running") {
            $summary.status = "interrupted"
            foreach ($item in $summary.steps) {
                if ($item.status -eq "running") {
                    $item.status = "interrupted"
                    $item.reason = "interrupted"
                }
            }
        }
        $summary.finishedAt = [DateTime]::UtcNow.ToString("o")
        Save-VerificationSummary -Summary $summary -LogDirectory $LogDirectory
        Write-Host "Summary: $(Join-Path $LogDirectory 'summary.md')"
    }
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
$websiteTests = [bool]$scope.verification.websiteTests
$websiteBuild = [bool]$scope.verification.websiteBuild
$websiteBrowser = [bool]$scope.verification.websiteBrowser

if ($Mode -eq "Preflight") {
    # Deliberately incomplete development feedback, never a merge/release gate.
    # Keep quality/tooling checks selected by the real change scope.
    $backendTests = "none"
    $frontendTests = "none"
    $e2eMode = "none"
    $frontendBuild = $false
    $websiteTests = $false
    $websiteBuild = $false
    $websiteBrowser = $false
}
elseif ($Mode -eq "Quick" -and -not [bool]$scope.flags.critical) {
    $e2eMode = "none"
    if ($scope.risk -ne "full") {
        $frontendBuild = $false
        $websiteBuild = $false
        $websiteBrowser = $false
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
    $websiteTests = $true
    $websiteBuild = $true
    $websiteBrowser = $true
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

if ($backendQuality) {
    if ($BackendRuntime -eq "Docker") {
        Add-Step -Steps $steps -Step (New-BackendStep -Id "backend-static" -Name "Backend Ruff, Black and Mypy" -Check "Static")
    }
    else {
        Add-Step -Steps $steps -Step (New-BackendStep -Id "backend-ruff" -Name "Backend Ruff" -Check "Ruff")
        Add-Step -Steps $steps -Step (New-BackendStep -Id "backend-black" -Name "Backend Black" -Check "Black")
        Add-Step -Steps $steps -Step (New-BackendStep -Id "backend-mypy" -Name "Backend Mypy" -Check "Mypy")
    }
}
if ($frontendQuality) {
    Add-Step -Steps $steps -Step (New-Step -Id "frontend-typecheck" -Name "Frontend typecheck" -WorkingDirectory (Join-Path $repoRoot "frontend") -Command "pnpm" -Arguments @("typecheck"))
    Add-Step -Steps $steps -Step (New-Step -Id "frontend-lint" -Name "Frontend lint" -WorkingDirectory (Join-Path $repoRoot "frontend") -Command "pnpm" -Arguments @("lint"))
}

if ($backendTests -ne "none") {
    $pytestTargets = @("tests")
    if ($backendTests -eq "targeted" -and @($scope.targets.backend).Count -gt 0) {
        $pytestTargets = @($scope.targets.backend | ForEach-Object { "$_" })
    }
    Add-Step -Steps $steps -Step (New-BackendStep -Id "backend-tests" -Name "Backend tests ($backendTests)" -Check "Tests" -Arguments ($pytestTargets + @("-q", "--tb=short")))
}

if ($frontendTests -ne "none") {
    $vitestTargets = @("tests")
    if ($frontendTests -eq "targeted" -and @($scope.targets.frontend).Count -gt 0) {
        $vitestTargets = @($scope.targets.frontend | ForEach-Object { "$_" })
    }
    Add-Step -Steps $steps -Step (New-Step -Id "frontend-tests" -Name "Frontend tests ($frontendTests)" -WorkingDirectory (Join-Path $repoRoot "frontend") -Command "pnpm" -Arguments (@("exec", "vitest", "run") + $vitestTargets + @("--reporter=dot")))
}
if ($frontendBuild) {
    Add-Step -Steps $steps -Step (New-Step -Id "frontend-build" -Name "Frontend production build" -WorkingDirectory (Join-Path $repoRoot "frontend") -Command "pnpm" -Arguments @("build"))
}

if ($websiteTests) {
    Add-Step -Steps $steps -Step (New-Step -Id "website-unit" -Name "Public website static and unit checks" -WorkingDirectory $repoRoot -Command "node" -Arguments @("website/scripts/test.mjs"))
}
if ($websiteBuild) {
    Add-Step -Steps $steps -Step (New-Step -Id "website-build" -Name "Public website production build" -WorkingDirectory $repoRoot -Command "node" -Arguments @("website/scripts/build.mjs", "--production") -Environment @{
        APP_URL = "https://app.example.test/login"
        WEBSITE_OUTPUT_DIR = ".codex/website-tests/production-verification"
    })
}
if ($websiteBrowser) {
    Add-Step -Steps $steps -Step (New-Step -Id "website-browser" -Name "Public website browser checks" -WorkingDirectory $repoRoot -Command "node" -Arguments @("website/scripts/check-browser.mjs"))
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
    backendRuntime = $BackendRuntime.ToLowerInvariant()
    baseRef = $BaseRef
    warning = if ($Mode -eq "Preflight") { "Preflight omits application tests and builds; Auto/Full and the GitHub CI gate remain mandatory." } elseif ($Mode -eq "Quick") { "Quick is for development feedback only; the GitHub CI gate remains mandatory." } else { $null }
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
    if ($plan.warning) {
        Write-Host "  Note:  $($plan.warning)" -ForegroundColor Yellow
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
if ($plan.warning) {
    Write-Host "  $($plan.warning)" -ForegroundColor Yellow
}

$logRoot = Join-Path (Join-Path $repoRoot ".codex") "verification"
$logDirectory = Join-Path $logRoot ([Guid]::NewGuid().ToString("N"))
[IO.Directory]::CreateDirectory($logDirectory) | Out-Null
try {
    $revision = (& git -C $repoRoot rev-parse HEAD | Out-String).Trim()
    Invoke-VerificationSteps -Steps @($steps) -LogDirectory $logDirectory -Mode $Mode -Risk $scope.risk -Revision $revision -ChangedPathCount @($scope.paths).Count
}
finally {
    # Stable entry point for the next assistant turn; the per-run report remains
    # authoritative when independent runs finish at different times.
    foreach ($extension in @("json", "md")) {
        $source = Join-Path $logDirectory "summary.$extension"
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination (Join-Path $logRoot "latest.$extension") -Force
        }
    }
}

Write-Host ("Verification passed: {0} steps." -f $steps.Count) -ForegroundColor Green
Write-Host "Detailed logs: $logDirectory"
