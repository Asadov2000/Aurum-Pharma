[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scopeScript = Join-Path $PSScriptRoot "task-scope.ps1"
$verifyScript = Join-Path $PSScriptRoot "verify-change.ps1"
$e2eScript = Join-Path $PSScriptRoot "e2e-isolated.ps1"
$whitespaceScript = Join-Path $PSScriptRoot "check-untracked-whitespace.ps1"
$localLauncherScript = Join-Path $PSScriptRoot "start-local-demo-admin.ps1"
$cmdLauncher = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")) "Start-Aurum-Pharma-Admin.cmd"
$powershellExecutable = (Get-Process -Id $PID).Path
$script:assertions = 0

function Get-Scope {
    param([AllowEmptyCollection()][string[]]$Paths)

    $json = & $scopeScript -BaseRef "origin/main" -ChangedPath $Paths -Format Json
    return (($json -join [Environment]::NewLine) | ConvertFrom-Json)
}

function Get-Plan {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("Auto", "Quick", "Full")][string]$Mode,
        [AllowEmptyCollection()][string[]]$Paths
    )

    $json = & $verifyScript -Mode $Mode -BaseRef "origin/main" -ChangedPath $Paths -PlanOnly -Format Json
    return (($json -join [Environment]::NewLine) | ConvertFrom-Json)
}

function Assert-Equal {
    param(
        [AllowNull()][object]$Actual,
        [AllowNull()][object]$Expected,
        [Parameter(Mandatory = $true)][string]$Because
    )

    $script:assertions++
    if ("$Actual" -cne "$Expected") {
        throw "Assertion failed ($Because). Expected '$Expected', got '$Actual'."
    }
}

function Assert-Contains {
    param(
        [AllowEmptyCollection()][object[]]$Actual,
        [Parameter(Mandatory = $true)][string]$Expected,
        [Parameter(Mandatory = $true)][string]$Because
    )

    $script:assertions++
    $values = @($Actual | ForEach-Object { "$_" })
    if ($values -cnotcontains $Expected) {
        throw "Assertion failed ($Because). '$Expected' is missing from [$($values -join ', ')]."
    }
}

function Assert-Count {
    param(
        [AllowEmptyCollection()][object[]]$Actual,
        [Parameter(Mandatory = $true)][int]$Expected,
        [Parameter(Mandatory = $true)][string]$Because
    )

    $script:assertions++
    $actualCount = @($Actual).Count
    if ($actualCount -ne $Expected) {
        throw "Assertion failed ($Because). Expected $Expected items, got $actualCount."
    }
}

$empty = Get-Scope -Paths @()
Assert-Equal -Actual $empty.risk -Expected "none" -Because "an explicit empty path set is safe"
Assert-Count -Actual @($empty.paths) -Expected 0 -Because "an empty path set stays empty"

$docs = Get-Scope -Paths @("docs/adr/9999-example.md")
Assert-Equal -Actual $docs.risk -Expected "docs" -Because "documentation does not run app tests"
Assert-Equal -Actual $docs.verification.backendTests -Expected "none" -Because "docs skip backend"
Assert-Equal -Actual $docs.verification.frontendTests -Expected "none" -Because "docs skip frontend"

$catalogBackend = Get-Scope -Paths @("backend/app/domains/catalog/service.py")
Assert-Equal -Actual $catalogBackend.risk -Expected "integrated" -Because "catalog affects stock workflows"
Assert-Equal -Actual $catalogBackend.verification.backendTests -Expected "targeted" -Because "catalog uses focused backend tests"
Assert-Contains -Actual @($catalogBackend.targets.backend) -Expected "tests/domains/catalog" -Because "catalog domain tests are selected"
Assert-Contains -Actual @($catalogBackend.targets.backend) -Expected "tests/isolation/test_catalog_rls.py" -Because "catalog RLS is selected"
Assert-Contains -Actual @($catalogBackend.targets.e2e) -Expected "e2e/catalog-flow.spec.ts" -Because "catalog flow is selected"

$catalogFrontend = Get-Scope -Paths @("frontend/src/features/catalog/api.ts")
Assert-Equal -Actual $catalogFrontend.verification.frontendTests -Expected "targeted" -Because "catalog UI uses focused Vitest"
Assert-Contains -Actual @($catalogFrontend.targets.frontend) -Expected "tests/features/catalog" -Because "catalog UI tests are selected"

$pos = Get-Scope -Paths @("backend/app/domains/pos/service.py")
Assert-Equal -Actual $pos.risk -Expected "critical" -Because "POS is critical"
Assert-Equal -Actual $pos.verification.backendTests -Expected "full" -Because "POS runs all backend tests"
Assert-Equal -Actual $pos.verification.frontendTests -Expected "full" -Because "POS runs all frontend tests"
Assert-Equal -Actual $pos.verification.e2e -Expected "full" -Because "POS runs all E2E"

$migration = Get-Scope -Paths @("backend/alembic/versions/9999_example.py")
Assert-Equal -Actual $migration.risk -Expected "critical" -Because "migrations are critical"
Assert-Equal -Actual $migration.verification.e2e -Expected "full" -Because "migrations run all E2E"

$mfaRotation = Get-Scope -Paths @("backend/app/maintenance/rotate_mfa_key.py")
Assert-Equal -Actual $mfaRotation.risk -Expected "critical" -Because "MFA key rotation is security critical"
Assert-Equal -Actual $mfaRotation.verification.e2e -Expected "full" -Because "MFA rotation runs all E2E"

$sharedUi = Get-Scope -Paths @("frontend/src/components/ui/Button.tsx")
Assert-Equal -Actual $sharedUi.risk -Expected "integrated" -Because "shared UI has a wide frontend impact"
Assert-Equal -Actual $sharedUi.verification.frontendTests -Expected "full" -Because "shared UI runs all Vitest"
Assert-Equal -Actual $sharedUi.verification.frontendBuild -Expected $true -Because "shared UI requires a production build"
Assert-Contains -Actual @($sharedUi.targets.e2e) -Expected "e2e/interface-layout.spec.ts" -Because "shared UI checks layout"

$singleE2E = Get-Scope -Paths @("frontend/e2e/catalog-flow.spec.ts")
Assert-Equal -Actual $singleE2E.verification.backendTests -Expected "none" -Because "an isolated E2E spec does not run backend unit tests"
Assert-Equal -Actual $singleE2E.verification.frontendTests -Expected "none" -Because "an isolated E2E spec does not run Vitest"
Assert-Equal -Actual $singleE2E.verification.e2e -Expected "targeted" -Because "an isolated E2E spec stays targeted"
Assert-Count -Actual @($singleE2E.targets.e2e) -Expected 1 -Because "only the changed E2E spec is selected"

$backendHelper = Get-Scope -Paths @("backend/tests/conftest.py")
Assert-Contains -Actual @($backendHelper.targets.backend) -Expected "tests" -Because "backend conftest selects the test suite"
$frontendHelper = Get-Scope -Paths @("frontend/tests/setup.ts")
Assert-Contains -Actual @($frontendHelper.targets.frontend) -Expected "tests" -Because "frontend setup selects the test suite"

$e2eFramework = Get-Scope -Paths @("frontend/e2e/helpers.ts")
Assert-Equal -Actual $e2eFramework.risk -Expected "full" -Because "shared E2E helpers affect every browser flow"
Assert-Equal -Actual $e2eFramework.verification.e2e -Expected "full" -Because "shared E2E helpers run all E2E"

$tooling = Get-Scope -Paths @("scripts/task-scope.ps1")
Assert-Equal -Actual $tooling.risk -Expected "tooling" -Because "verification tooling is self-contained"
Assert-Equal -Actual $tooling.verification.toolingSelfTest -Expected $true -Because "verification tooling tests itself"
Assert-Equal -Actual $tooling.verification.backendTests -Expected "none" -Because "verification tooling skips app tests"

$unknown = Get-Scope -Paths @("unknown/new-area/file.xyz")
Assert-Equal -Actual $unknown.risk -Expected "full" -Because "unknown roots fail closed"
Assert-Contains -Actual @($unknown.reasons) -Expected "unknown-path" -Because "unknown roots explain the escalation"

$fakeAuth = Get-Scope -Paths @("backend/app/domains/authentication_fake/service.py")
Assert-Equal -Actual $fakeAuth.risk -Expected "full" -Because "near-miss domain names do not bypass classification"

$crossStack = Get-Scope -Paths @(
    "backend/app/domains/catalog/service.py",
    "frontend/src/features/catalog/api.ts"
)
Assert-Equal -Actual $crossStack.risk -Expected "critical" -Because "cross-stack changes get the full gate"
Assert-Contains -Actual @($crossStack.reasons) -Expected "cross-stack" -Because "cross-stack escalation is visible"

$externalScopeArguments = @("-NoProfile")
if ($env:OS -eq "Windows_NT") {
    $externalScopeArguments += @("-ExecutionPolicy", "Bypass")
}
$externalScopeArguments += @(
    "-File", $scopeScript,
    "-ChangedPath", "backend/app/domains/catalog/service.py", "frontend/src/features/catalog/api.ts",
    "-Format", "Json"
)
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $powershellExecutable @externalScopeArguments *> $null
    $externalScopeExitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $previousPreference
}
Assert-Equal -Actual ($externalScopeExitCode -ne 0) -Expected $true -Because "ambiguous external multi-path syntax fails closed"

$crossStackPlan = Get-Plan -Mode "Auto" -Paths @(
    "backend/app/domains/catalog/service.py",
    "frontend/src/features/catalog/api.ts"
)
Assert-Equal -Actual $crossStackPlan.scope.risk -Expected "critical" -Because "the planner preserves every in-process changed path"
Assert-Contains -Actual @($crossStackPlan.scope.reasons) -Expected "cross-stack" -Because "the planner preserves cross-stack escalation"

$normalized = Get-Scope -Paths @(
    "frontend\src\features\catalog\api.ts",
    "frontend/src/features/catalog/api.ts"
)
Assert-Count -Actual @($normalized.paths) -Expected 1 -Because "path separators and duplicates are normalized"

$runtimeSurface = Get-Scope -Paths @("frontend/src/lib/pwa.ts")
Assert-Equal -Actual $runtimeSurface.risk -Expected "integrated" -Because "PWA runtime changes require browser coverage"
Assert-Contains -Actual @($runtimeSurface.targets.e2e) -Expected "e2e/pwa.spec.ts" -Because "PWA flow is selected"
Assert-Contains -Actual @($runtimeSurface.targets.e2e) -Expected "e2e/runtime-surface.spec.ts" -Because "runtime surface is selected"

$autoCatalog = Get-Plan -Mode "Auto" -Paths @("backend/app/domains/catalog/service.py")
$autoCatalogE2E = @($autoCatalog.steps | Where-Object { $_.id -eq "e2e" })
Assert-Count -Actual $autoCatalogE2E -Expected 1 -Because "Auto schedules one isolated E2E step"
Assert-Contains -Actual @($autoCatalogE2E[0].arguments) -Expected "e2e/catalog-flow.spec.ts" -Because "Auto passes the catalog flow to Playwright"
Assert-Contains -Actual @($autoCatalogE2E[0].arguments) -Expected "e2e/catalog-import.spec.ts" -Because "Auto passes the catalog import flow to Playwright"

$e2eCommand = Get-Command -Name $e2eScript
$remainingArgumentAttributes = @(
    $e2eCommand.Parameters["PlaywrightArgs"].Attributes |
        Where-Object {
            $_ -is [System.Management.Automation.ParameterAttribute] -and
            $_.ValueFromRemainingArguments
        }
)
Assert-Count -Actual $remainingArgumentAttributes -Expected 1 -Because "isolated E2E accepts multiple Playwright specs"

$e2eBindingArguments = @("-NoProfile")
if ($env:OS -eq "Windows_NT") {
    $e2eBindingArguments += @("-ExecutionPolicy", "Bypass")
}
$e2eBindingArguments += @(
    "-File", $e2eScript, "-PlanOnly",
    "e2e/catalog-flow.spec.ts", "e2e/catalog-import.spec.ts"
)
$boundE2EJson = & $powershellExecutable @e2eBindingArguments
Assert-Equal -Actual $LASTEXITCODE -Expected 0 -Because "external E2E argument binding succeeds"
$boundE2E = @((($boundE2EJson -join [Environment]::NewLine) | ConvertFrom-Json))
Assert-Count -Actual $boundE2E -Expected 2 -Because "external E2E binding preserves both specs"
Assert-Contains -Actual $boundE2E -Expected "e2e/catalog-import.spec.ts" -Because "external E2E binding preserves the final spec"

$quickCritical = Get-Plan -Mode "Quick" -Paths @("backend/app/domains/pos/service.py")
Assert-Equal -Actual $quickCritical.scope.risk -Expected "critical" -Because "Quick sees critical risk"
Assert-Contains -Actual @($quickCritical.steps.id) -Expected "e2e" -Because "Quick never weakens critical verification"
Assert-Contains -Actual @($quickCritical.steps.id) -Expected "frontend-build" -Because "Quick keeps the critical build"

$quickCatalog = Get-Plan -Mode "Quick" -Paths @("backend/app/domains/catalog/service.py")
Assert-Equal -Actual (@($quickCatalog.steps.id) -contains "e2e") -Expected $false -Because "Quick omits integrated E2E during the edit loop"
Assert-Contains -Actual @($quickCatalog.steps.id) -Expected "backend-tests" -Because "Quick keeps targeted catalog tests"

$fullDocs = Get-Plan -Mode "Full" -Paths @("docs/adr/9999-example.md")
Assert-Contains -Actual @($fullDocs.steps.id) -Expected "backend-tests" -Because "Full always runs backend tests"
Assert-Contains -Actual @($fullDocs.steps.id) -Expected "frontend-tests" -Because "Full always runs frontend tests"
Assert-Contains -Actual @($fullDocs.steps.id) -Expected "e2e" -Because "Full always runs E2E"

$launcherArguments = @("-NoProfile")
if ($env:OS -eq "Windows_NT") {
    $launcherArguments += @("-ExecutionPolicy", "Bypass")
}
$launcherArguments += @("-File", $localLauncherScript, "-DryRun", "-NoBrowser")
$launcherOutput = & $powershellExecutable @launcherArguments
Assert-Equal -Actual $LASTEXITCODE -Expected 0 -Because "the local launcher dry run succeeds"
$launcherText = $launcherOutput -join [Environment]::NewLine
Assert-Equal `
    -Actual ($launcherText -match "Dry run complete") `
    -Expected $true `
    -Because "the local launcher delegates to the isolated showcase stack"
Assert-Equal `
    -Actual ($launcherText -match "billing-worker") `
    -Expected $true `
    -Because "the local launcher starts the billing worker"
Assert-Equal `
    -Actual ($launcherText -match "Verify shared frontend matches local frontend") `
    -Expected $true `
    -Because "the local launcher rejects a stale shared frontend"

if ($env:OS -eq "Windows_NT") {
    $cmdLauncherOutput = & cmd.exe /d /c "`"$cmdLauncher`" -DryRun -NoBrowser"
    Assert-Equal -Actual $LASTEXITCODE -Expected 0 -Because "the desktop CMD launcher succeeds"
    Assert-Equal `
        -Actual (($cmdLauncherOutput -join [Environment]::NewLine) -match "Dry run complete") `
        -Expected $true `
        -Because "the desktop CMD launcher forwards safe dry-run arguments"
}

$fixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ("aurum-whitespace-test-" + [Guid]::NewGuid().ToString("N"))
[IO.Directory]::CreateDirectory($fixtureRoot) | Out-Null
try {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    $goodFixture = Join-Path $fixtureRoot "good.txt"
    $badFixture = Join-Path $fixtureRoot "bad.txt"
    [IO.File]::WriteAllText($goodFixture, "clean`n", $encoding)
    [IO.File]::WriteAllText($badFixture, "trailing   `n", $encoding)

    $whitespaceArguments = @("-NoProfile")
    if ($env:OS -eq "Windows_NT") {
        $whitespaceArguments += @("-ExecutionPolicy", "Bypass")
    }
    $whitespaceArguments += @("-File", $whitespaceScript, "-Path", $goodFixture)
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $powershellExecutable @whitespaceArguments *> $null
        $goodExitCode = $LASTEXITCODE

        $whitespaceArguments[-1] = $badFixture
        & $powershellExecutable @whitespaceArguments *> $null
        $badExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    Assert-Equal -Actual $goodExitCode -Expected 0 -Because "clean untracked text passes whitespace inspection"
    Assert-Equal -Actual ($badExitCode -ne 0) -Expected $true -Because "trailing whitespace in an untracked file fails"
}
finally {
    $resolvedFixtureRoot = [IO.Path]::GetFullPath($fixtureRoot)
    $resolvedTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $resolvedFixtureRoot.StartsWith($resolvedTempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a fixture directory outside the system temp directory."
    }
    if ([IO.Directory]::Exists($resolvedFixtureRoot)) {
        [IO.Directory]::Delete($resolvedFixtureRoot, $true)
    }
}

Write-Host "Verification tooling self-test passed: $script:assertions assertions." -ForegroundColor Green
exit 0
