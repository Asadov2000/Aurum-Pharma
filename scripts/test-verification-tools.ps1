[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scopeScript = Join-Path $PSScriptRoot "task-scope.ps1"
$verifyScript = Join-Path $PSScriptRoot "verify-change.ps1"
$e2eScript = Join-Path $PSScriptRoot "e2e-isolated.ps1"
$whitespaceScript = Join-Path $PSScriptRoot "check-untracked-whitespace.ps1"
$localLauncherScript = Join-Path $PSScriptRoot "start-local-demo-admin.ps1"
$currentMainLauncherScript = Join-Path $PSScriptRoot "start-current-main-demo.ps1"
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
        [AllowEmptyCollection()][string[]]$Paths,
        [ValidateSet("Docker", "Poetry")][string]$BackendRuntime
    )

    $runtimeArguments = @{}
    if ($BackendRuntime) { $runtimeArguments.BackendRuntime = $BackendRuntime }
    $json = & $verifyScript -Mode $Mode -BaseRef "origin/main" -ChangedPath $Paths -PlanOnly -Format Json @runtimeArguments
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

$oneClickScope = Get-Scope -Paths @("Check-Aurum-Pharma.cmd")
Assert-Equal -Actual $oneClickScope.risk -Expected "tooling" -Because "the root one-click verifier is classified as verification tooling"
Assert-Equal -Actual $oneClickScope.verification.toolingSelfTest -Expected $true -Because "the root launcher keeps tooling self-tests mandatory"

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

foreach ($path in @("scripts/test-backend-isolated.ps1")) {
    $backendTooling = Get-Scope -Paths @($path)
    Assert-Equal -Actual $backendTooling.risk -Expected "tooling" -Because "$path selects lightweight contract checks"
    Assert-Equal -Actual $backendTooling.verification.toolingSelfTest -Expected $true -Because "$path runs its guards"
}
foreach ($path in @("scripts/backend-isolated.ps1", "scripts/backend-isolated-guards.ps1")) {
    $backendInfrastructure = Get-Scope -Paths @($path)
    Assert-Equal -Actual $backendInfrastructure.risk -Expected "full" -Because "$path changes execution or cleanup of real infrastructure"
}
$verificationStack = Get-Scope -Paths @("docker-compose.verify.yml")
Assert-Equal -Actual $verificationStack.risk -Expected "full" -Because "verification stack changes require actual application checks"

$unknown = Get-Scope -Paths @("unknown/new-area/file.xyz")
Assert-Equal -Actual $unknown.risk -Expected "full" -Because "unknown roots fail closed"
Assert-Contains -Actual @($unknown.reasons) -Expected "unknown-path" -Because "unknown roots explain the escalation"

foreach ($path in @("website/src/index.html", "website/assets/site.css", "website/scripts/test.mjs", "website/new-area/future.xyz")) {
    $website = Get-Scope -Paths @($path)
    Assert-Equal -Actual $website.risk -Expected "standard" -Because "the standalone website is isolated from the application"
    Assert-Equal -Actual $website.flags.website -Expected $true -Because "website files have their own area"
    Assert-Equal -Actual $website.verification.websiteTests -Expected $true -Because "every website change selects its checks"
    Assert-Equal -Actual $website.verification.backendTests -Expected "none" -Because "website changes do not require a database"
    Assert-Equal -Actual $website.verification.frontendTests -Expected "none" -Because "website changes do not run application Vitest"
    Assert-Equal -Actual $website.verification.e2e -Expected "none" -Because "website changes do not start the application E2E stack"
}
$websiteDocs = Get-Scope -Paths @("website/README.md")
Assert-Equal -Actual $websiteDocs.risk -Expected "docs" -Because "website instructions remain documentation"
Assert-Equal -Actual $websiteDocs.verification.websiteTests -Expected $false -Because "documentation alone needs no website checks"
$websiteNearMiss = Get-Scope -Paths @("website_fake/index.html")
Assert-Equal -Actual $websiteNearMiss.risk -Expected "full" -Because "near-miss website roots do not bypass classification"
$websiteInfrastructure = Get-Scope -Paths @("website/Dockerfile")
Assert-Equal -Actual $websiteInfrastructure.risk -Expected "full" -Because "website infrastructure keeps the existing infrastructure gate"

$websitePlan = Get-Plan -Mode "Auto" -Paths @("website/src/index.html")
$websiteStepIds = @($websitePlan.steps.id | Where-Object { $_ -like "website-*" })
Assert-Equal -Actual ($websiteStepIds -join ",") -Expected "website-unit,website-build,website-browser" -Because "Auto runs all website checks in order"
Assert-Count -Actual @($websitePlan.steps | Where-Object { $_.id -like "backend-*" -or $_.id -like "frontend-*" -or $_.id -eq "e2e" }) -Expected 0 -Because "website Auto has no application infrastructure dependency"
foreach ($check in @(
    @{ Id = "website-unit"; Script = "website/scripts/test.mjs" },
    @{ Id = "website-build"; Script = "website/scripts/build.mjs" },
    @{ Id = "website-browser"; Script = "website/scripts/check-browser.mjs" }
)) {
    $websiteStep = @($websitePlan.steps | Where-Object { $_.id -eq $check.Id })[0]
    Assert-Equal -Actual $websiteStep.command -Expected "node" -Because "website checks use the existing Node runtime"
    Assert-Contains -Actual $websiteStep.arguments -Expected $check.Script -Because "the planned website entrypoint is explicit"
    Assert-Equal -Actual $websiteStep.cwd -Expected (Resolve-Path (Join-Path $PSScriptRoot "..")).Path -Because "website checks resolve paths from the repository root"
}
$websiteBuildStep = @($websitePlan.steps | Where-Object { $_.id -eq "website-build" })[0]
Assert-Contains -Actual $websiteBuildStep.arguments -Expected "--production" -Because "Auto validates production configuration"
Assert-Equal -Actual $websiteBuildStep.environment.APP_URL -Expected "https://app.example.test/login" -Because "verification uses an explicit non-production application address"
Assert-Equal -Actual $websiteBuildStep.environment.WEBSITE_OUTPUT_DIR -Expected ".codex/website-tests/production-verification" -Because "verification must not replace the user's local website preview"
$quickWebsite = Get-Plan -Mode "Quick" -Paths @("website/src/index.html")
Assert-Equal -Actual ((@($quickWebsite.steps.id | Where-Object { $_ -like "website-*" })) -join ",") -Expected "website-unit" -Because "Quick keeps fast website tests while Auto provides the full website gate"

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

$autoCatalog = & {
    function docker { throw "PlanOnly must not invoke Docker." }
    function poetry { throw "PlanOnly must not invoke Poetry." }
    Get-Plan -Mode "Auto" -Paths @("backend/app/domains/catalog/service.py")
}
$autoCatalogE2E = @($autoCatalog.steps | Where-Object { $_.id -eq "e2e" })
Assert-Count -Actual $autoCatalogE2E -Expected 1 -Because "Auto schedules one isolated E2E step"
Assert-Contains -Actual @($autoCatalogE2E[0].arguments) -Expected "e2e/catalog-flow.spec.ts" -Because "Auto passes the catalog flow to Playwright"
Assert-Contains -Actual @($autoCatalogE2E[0].arguments) -Expected "e2e/catalog-import.spec.ts" -Because "Auto passes the catalog import flow to Playwright"

$poetryCatalog = Get-Plan -Mode "Auto" -Paths @("backend/app/domains/catalog/service.py") -BackendRuntime Poetry
foreach ($check in @(
    @{ Id = "backend-tests"; Check = "Tests"; Module = "pytest" },
    @{ Id = "backend-ruff"; Check = "Ruff"; Module = "ruff" },
    @{ Id = "backend-black"; Check = "Black"; Module = "black" },
    @{ Id = "backend-mypy"; Check = "Mypy"; Module = "mypy" }
)) {
    $dockerStep = @($autoCatalog.steps | Where-Object { $_.id -eq $check.Id })
    $poetryStep = @($poetryCatalog.steps | Where-Object { $_.id -eq $check.Id })
    Assert-Count -Actual $dockerStep -Expected 1 -Because "$($check.Id) keeps its stable step ID"
    Assert-Count -Actual $poetryStep -Expected 1 -Because "Poetry keeps $($check.Id)"
    Assert-Equal -Actual $dockerStep[0].command -Expected $powershellExecutable -Because "default backend checks use the isolated launcher"
    Assert-Contains -Actual $dockerStep[0].arguments -Expected (Join-Path $PSScriptRoot "backend-isolated.ps1") -Because "backend check runs the guarded entrypoint"
    $checkIndex = [Array]::IndexOf([object[]]$dockerStep[0].arguments, "-Check")
    Assert-Equal -Actual ($checkIndex -ge 0) -Expected $true -Because "the launcher check is explicit"
    Assert-Equal -Actual $dockerStep[0].arguments[$checkIndex + 1] -Expected $check.Check -Because "the correct isolated check is selected"
    Assert-Equal -Actual $poetryStep[0].command -Expected "poetry" -Because "explicit Poetry preserves the host runtime"
    Assert-Equal -Actual $poetryStep[0].arguments[1] -Expected $check.Module -Because "Poetry preserves the original tool"
}
$dockerTests = @($autoCatalog.steps | Where-Object { $_.id -eq "backend-tests" })[0]
$poetryTests = @($poetryCatalog.steps | Where-Object { $_.id -eq "backend-tests" })[0]
foreach ($target in @($autoCatalog.scope.targets.backend) + @("-q", "--tb=short")) {
    Assert-Contains -Actual $dockerTests.arguments -Expected $target -Because "Docker preserves the selected test target and output flags"
    Assert-Contains -Actual $poetryTests.arguments -Expected $target -Because "Poetry preserves the selected test target and output flags"
}

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
Assert-Contains -Actual @($quickCritical.steps.id) -Expected "website-browser" -Because "Quick does not weaken website verification for critical changes"

$quickCatalog = Get-Plan -Mode "Quick" -Paths @("backend/app/domains/catalog/service.py")
Assert-Equal -Actual (@($quickCatalog.steps.id) -contains "e2e") -Expected $false -Because "Quick omits integrated E2E during the edit loop"
Assert-Contains -Actual @($quickCatalog.steps.id) -Expected "backend-tests" -Because "Quick keeps targeted catalog tests"

$fullDocs = Get-Plan -Mode "Full" -Paths @("docs/adr/9999-example.md")
Assert-Contains -Actual @($fullDocs.steps.id) -Expected "backend-tests" -Because "Full always runs backend tests"
Assert-Contains -Actual @($fullDocs.steps.id) -Expected "frontend-tests" -Because "Full always runs frontend tests"
Assert-Contains -Actual @($fullDocs.steps.id) -Expected "e2e" -Because "Full always runs E2E"
foreach ($id in @("website-unit", "website-build", "website-browser")) {
    Assert-Contains -Actual @($fullDocs.steps.id) -Expected $id -Because "Full always runs $id"
}
$fullBackend = @($fullDocs.steps | Where-Object { $_.id -eq "backend-tests" })[0]
Assert-Contains -Actual $fullBackend.arguments -Expected "tests" -Because "Full keeps the complete backend suite inside Docker"

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
    -Actual ($launcherText -match "catalog-worker") `
    -Expected $true `
    -Because "the local launcher starts the catalog worker"
Assert-Equal `
    -Actual ($launcherText -match "Verify shared frontend matches local frontend") `
    -Expected $true `
    -Because "the local launcher rejects a stale shared frontend"
if ($launcherText -match "needs a one-time secret upgrade") {
    Assert-Equal `
        -Actual ($launcherText -match "normal launcher will add it without replacing existing secrets") `
        -Expected $true `
        -Because "dry run explains the non-destructive local secret upgrade"
}

$currentLauncherArguments = @("-NoProfile")
if ($env:OS -eq "Windows_NT") {
    $currentLauncherArguments += @("-ExecutionPolicy", "Bypass")
}
$currentLauncherArguments += @("-File", $currentMainLauncherScript, "-DryRun", "-NoBrowser")
$currentLauncherOutput = & $powershellExecutable @currentLauncherArguments
Assert-Equal -Actual $LASTEXITCODE -Expected 0 -Because "the current-main launcher dry run succeeds"
$currentLauncherText = $currentLauncherOutput -join [Environment]::NewLine
Assert-Equal `
    -Actual ($currentLauncherText -match "isolated runtime worktree") `
    -Expected $true `
    -Because "the current-main launcher explains the isolated update"
Assert-Equal `
    -Actual ($currentLauncherText -match "Dry run complete") `
    -Expected $true `
    -Because "the current-main launcher delegates to the safe local launcher"

if ($env:OS -eq "Windows_NT") {
    $cmdLauncherOutput = & cmd.exe /d /c "`"$cmdLauncher`" -DryRun -NoBrowser"
    Assert-Equal -Actual $LASTEXITCODE -Expected 0 -Because "the desktop CMD launcher succeeds"
    Assert-Equal `
        -Actual (($cmdLauncherOutput -join [Environment]::NewLine) -match "Dry run complete") `
        -Expected $true `
        -Because "the desktop CMD launcher forwards safe dry-run arguments"
}

$fixtureParent = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")) ".codex"
$fixtureRoot = Join-Path $fixtureParent ("aurum-whitespace-test-" + [Guid]::NewGuid().ToString("N"))
[IO.Directory]::CreateDirectory($fixtureRoot) | Out-Null
try {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    $goodFixture = Join-Path $fixtureRoot "good.txt"
    $badFixture = Join-Path $fixtureRoot "bad.txt"
    [IO.File]::WriteAllText($goodFixture, "clean`n", $encoding)
    [IO.File]::WriteAllText($badFixture, "trailing   `n", $encoding)

    $launcherFixtureScripts = Join-Path $fixtureRoot "scripts"
    $launcherFixtureState = Join-Path $fixtureRoot ".codex"
    [IO.Directory]::CreateDirectory($launcherFixtureScripts) | Out-Null
    [IO.Directory]::CreateDirectory($launcherFixtureState) | Out-Null
    $fixtureLauncher = Join-Path $launcherFixtureScripts "start-current-main-demo.ps1"
    $fixtureChild = Join-Path $launcherFixtureScripts "start-local-demo-admin.ps1"
    $fixtureWebsiteUrl = Join-Path $launcherFixtureState "demo-public-website-url"
    Copy-Item -LiteralPath $currentMainLauncherScript -Destination $fixtureLauncher
    [IO.File]::WriteAllText($fixtureChild, @'
param([switch]$DryRun, [switch]$NoBrowser)
if (-not $DryRun -or -not $NoBrowser) { exit 19 }
Write-Output "FIXTURE_WEBSITE=$env:VITE_PUBLIC_WEBSITE_URL"
'@, $encoding)
    $fixtureLauncherArguments = @("-NoProfile")
    if ($env:OS -eq "Windows_NT") {
        $fixtureLauncherArguments += @("-ExecutionPolicy", "Bypass")
    }
    $fixtureLauncherArguments += @("-File", $fixtureLauncher, "-DryRun", "-NoBrowser")
    $existingWebsiteUrl = [Environment]::GetEnvironmentVariable("VITE_PUBLIC_WEBSITE_URL", "Process")
    try {
        [Environment]::SetEnvironmentVariable("VITE_PUBLIC_WEBSITE_URL", $null, "Process")
        $fixtureOutput = & $powershellExecutable @fixtureLauncherArguments
        Assert-Equal -Actual $LASTEXITCODE -Expected 0 -Because "the launcher works without a saved website URL"
        Assert-Contains -Actual @($fixtureOutput) -Expected "FIXTURE_WEBSITE=" -Because "missing website configuration remains unset in the child"

        [IO.File]::WriteAllText($fixtureWebsiteUrl, "https://saved.example.test/`n", $encoding)
        $fixtureOutput = & $powershellExecutable @fixtureLauncherArguments
        Assert-Equal -Actual $LASTEXITCODE -Expected 0 -Because "the launcher accepts a saved HTTPS website URL"
        Assert-Contains -Actual @($fixtureOutput) -Expected "FIXTURE_WEBSITE=https://saved.example.test/" -Because "the saved URL reaches the child launcher"

        [IO.File]::WriteAllText($fixtureWebsiteUrl, "http://invalid.example.test/", $encoding)
        [Environment]::SetEnvironmentVariable("VITE_PUBLIC_WEBSITE_URL", "https://process.example.test/", "Process")
        $fixtureOutput = & $powershellExecutable @fixtureLauncherArguments
        Assert-Equal -Actual $LASTEXITCODE -Expected 0 -Because "an explicit process URL takes precedence over an invalid saved URL"
        Assert-Contains -Actual @($fixtureOutput) -Expected "FIXTURE_WEBSITE=https://process.example.test/" -Because "the process URL reaches the child unchanged"

        [Environment]::SetEnvironmentVariable("VITE_PUBLIC_WEBSITE_URL", $null, "Process")
        $fixtureOutput = & $powershellExecutable @fixtureLauncherArguments
        Assert-Equal -Actual ($LASTEXITCODE -ne 0) -Expected $true -Because "a saved HTTP website URL stops the launcher"
        Assert-Equal -Actual (($fixtureOutput -join "`n") -match "must contain an HTTPS website URL") -Expected $true -Because "invalid saved URLs receive a useful error"
        Assert-Equal -Actual (($fixtureOutput -join "`n") -match "FIXTURE_WEBSITE=") -Expected $false -Because "invalid saved URLs never reach the child launcher"
    }
    finally {
        [Environment]::SetEnvironmentVariable("VITE_PUBLIC_WEBSITE_URL", $existingWebsiteUrl, "Process")
    }

    & {
        # Dot-source only the side-effect-free planner, then exercise its real runner.
        . $verifyScript -Mode Auto -ChangedPath @("website/src/index.html") -PlanOnly -Format Json | Out-Null
        $existingValue = [Environment]::GetEnvironmentVariable("AURUM_VERIFY_ENV_EXISTING", "Process")
        $missingValue = [Environment]::GetEnvironmentVariable("AURUM_VERIFY_ENV_MISSING", "Process")
        try {
            [Environment]::SetEnvironmentVariable("AURUM_VERIFY_ENV_EXISTING", "inherited", "Process")
            [Environment]::SetEnvironmentVariable("AURUM_VERIFY_ENV_MISSING", $null, "Process")
            foreach ($expectedExit in @(0, 17)) {
                $originalLocation = (Get-Location).Path
                $probe = 'if ($env:AURUM_VERIFY_ENV_EXISTING -ne "scoped" -or $env:AURUM_VERIFY_ENV_MISSING -ne "temporary") { exit 23 }; exit ' + $expectedExit
                # Windows PowerShell 5.1 strips nested quotes in native -Command arguments.
                $encodedProbe = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($probe))
                $environmentStep = New-Step -Id "environment-$expectedExit" -Name "Environment isolation fixture" -WorkingDirectory $fixtureRoot -Command $powershellExecutable -Arguments @("-NoProfile", "-EncodedCommand", $encodedProbe) -Environment @{
                    AURUM_VERIFY_ENV_EXISTING = "scoped"
                    AURUM_VERIFY_ENV_MISSING = "temporary"
                }
                $caughtFailure = $false
                $stepResult = $null
                try {
                    $stepResult = Invoke-LoggedStep -Step $environmentStep -LogDirectory $fixtureRoot -Number 1 -Total 1 6> $null
                }
                catch {
                    $caughtFailure = $true
                    $stepResult = $_.Exception.Data['StepResult']
                }
                Assert-Equal -Actual $caughtFailure -Expected ($expectedExit -ne 0) -Because "the environment fixture propagates its command result"
                Assert-Equal -Actual ($null -ne $stepResult) -Expected $true -Because "success and failure both expose a structured step result"
                Assert-Equal -Actual $stepResult.exitCode -Expected $expectedExit -Because "the step result preserves the native exit code"
                Assert-Equal -Actual $stepResult.status -Expected $(if ($expectedExit -eq 0) { "passed" } else { "failed" }) -Because "the step result does not report failure as success"
                Assert-Equal -Actual ((Get-Location).Path) -Expected $originalLocation -Because "the working directory is restored after success or failure"
                Assert-Equal -Actual ([Environment]::GetEnvironmentVariable("AURUM_VERIFY_ENV_EXISTING", "Process")) -Expected "inherited" -Because "step environment is restored after success or failure"
                Assert-Equal -Actual ([Environment]::GetEnvironmentVariable("AURUM_VERIFY_ENV_MISSING", "Process")) -Expected $null -Because "new environment keys do not leak into later checks"
            }
        }
        finally {
            [Environment]::SetEnvironmentVariable("AURUM_VERIFY_ENV_EXISTING", $existingValue, "Process")
            [Environment]::SetEnvironmentVariable("AURUM_VERIFY_ENV_MISSING", $missingValue, "Process")
        }

        function New-ReportFixtureStep {
            param([string]$Id, [string]$Code)
            # PS5.1 module autoload emits CLIXML progress on stderr. Suppress
            # progress so our deliberate raw native stderr stays a valid stream.
            $childCode = '$ProgressPreference = ''SilentlyContinue''; ' + $Code
            $encodedCode = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($childCode))
            return New-Step -Id $Id -Name "Report fixture $Id" -WorkingDirectory $fixtureRoot -Command $powershellExecutable -Arguments @("-NoProfile", "-EncodedCommand", $encodedCode)
        }

        $summaryJson = Join-Path $fixtureRoot "summary.json"
        $summaryMarkdown = Join-Path $fixtureRoot "summary.md"
        $sentinelFile = Join-Path $fixtureRoot "unexpected-sentinel.txt"
        $sentinelCode = '[IO.File]::WriteAllText(''' + $sentinelFile.Replace("'", "''") + ''', ''must not run''); exit 0'
        $sentinelStep = New-ReportFixtureStep -Id "report-sentinel" -Code $sentinelCode
        $passStep = New-ReportFixtureStep -Id "report-pass" -Code 'Write-Output "fixture passed"; exit 0'
        $secondPassStep = New-ReportFixtureStep -Id "report-second-pass" -Code '$report = Get-Content -LiteralPath summary.json -Raw | ConvertFrom-Json; if ($report.status -ne "running" -or $report.steps[0].status -ne "passed" -or $report.steps[1].status -ne "running") { exit 29 }; [Console]::Error.WriteLine("STDERR_ZERO_42"); exit 0'

        $successOutput = @(Invoke-VerificationSteps -Steps @($passStep, $secondPassStep) -LogDirectory $fixtureRoot -Mode Auto -Risk tooling *>&1)
        $successReport = Get-Content -LiteralPath $summaryJson -Raw | ConvertFrom-Json
        Assert-Equal -Actual $successReport.version -Expected 1 -Because "execution reports declare their schema version"
        Assert-Equal -Actual $successReport.status -Expected "passed" -Because "the run passes only when every command succeeds"
        Assert-Count -Actual @($successReport.steps) -Expected 2 -Because "the report retains every planned step"
        foreach ($result in $successReport.steps) {
            Assert-Equal -Actual $result.status -Expected "passed" -Because "both completed commands are successful"
            Assert-Equal -Actual $result.exitCode -Expected 0 -Because "successful native commands retain exit zero"
            Assert-Equal -Actual ($result.seconds -ge 0) -Expected $true -Because "elapsed time is recorded"
            Assert-Equal -Actual (Test-Path -LiteralPath $result.log -PathType Leaf) -Expected $true -Because "each completed step links to an existing detailed log"
        }
        Assert-Equal -Actual (Test-Path -LiteralPath $summaryMarkdown -PathType Leaf) -Expected $true -Because "a readable report accompanies the JSON report"
        Assert-Equal -Actual ((Get-Content -LiteralPath $successReport.steps[1].log -Raw).Contains("STDERR_ZERO_42")) -Expected $true -Because "native stderr with exit zero is logged without a false failure"

        # These are deliberately synthetic values, never credentials from the environment.
        $syntheticSecret = "fixture-sensitive-token-UNIQUE-DO-NOT-REPORT"
        $noisyCode = '1..300 | ForEach-Object { Write-Output (''fixture-sensitive-token-UNIQUE-DO-NOT-REPORT '' + (''x'' * 300)) }; [Console]::Error.WriteLine(''fixture-private@example.invalid''); exit 17'
        $failStep = New-ReportFixtureStep -Id "report-fail" -Code $noisyCode
        $failureCaught = $false
        $safeFailure = ""
        $failureOutput = @(& {
            try {
                Invoke-VerificationSteps -Steps @($passStep, $failStep, $sentinelStep) -LogDirectory $fixtureRoot -Mode Auto -Risk tooling
            }
            catch {
                Set-Variable -Name failureCaught -Value $true -Scope 1
                Set-Variable -Name safeFailure -Value $_.Exception.Message -Scope 1
            }
        } *>&1)
        Assert-Equal -Actual $failureCaught -Expected $true -Because "a failed run throws instead of returning a success signal"
        $failureReport = Get-Content -LiteralPath $summaryJson -Raw | ConvertFrom-Json
        Assert-Equal -Actual $failureReport.status -Expected "failed" -Because "one native failure fails the run"
        Assert-Equal -Actual ($failureReport.steps.status -join ",") -Expected "passed,failed,not_run" -Because "the report distinguishes completed and unattempted checks"
        Assert-Equal -Actual $failureReport.steps[1].exitCode -Expected 17 -Because "the summary keeps the failing process exit code"
        Assert-Equal -Actual $failureReport.steps[1].reason -Expected "exit_code" -Because "failure reasons are safe structured categories"
        Assert-Equal -Actual ($null -eq $failureReport.steps[2].exitCode) -Expected $true -Because "an unattempted command cannot claim exit zero"
        Assert-Equal -Actual (Test-Path -LiteralPath $sentinelFile) -Expected $false -Because "later commands are not run after failure"
        $compactText = (@($failureOutput) -join "`n") + $safeFailure + (Get-Content -LiteralPath $summaryJson -Raw) + (Get-Content -LiteralPath $summaryMarkdown -Raw)
        Assert-Equal -Actual $compactText.Contains($syntheticSecret) -Expected $false -Because "raw log secrets never enter compact output or reports"
        Assert-Equal -Actual $compactText.Contains("fixture-private@example.invalid") -Expected $false -Because "stderr private values never enter compact output or reports"
        Assert-Equal -Actual ($compactText.Length -lt 16384) -Expected $true -Because "compact output remains bounded despite a large child log"
        Assert-Equal -Actual ((Get-Content -LiteralPath $failureReport.steps[1].log -Raw).Contains($syntheticSecret)) -Expected $true -Because "the noisy fixture really produced detailed output"

        $missingStep = New-Step -Id "report-missing" -Name "Missing command fixture" -WorkingDirectory $fixtureRoot -Command (Join-Path $fixtureRoot "missing-command.exe") -Arguments @("unused")
        $missingCaught = $false
        try {
            Invoke-VerificationSteps -Steps @($passStep, $missingStep, $sentinelStep) -LogDirectory $fixtureRoot -Mode Auto -Risk tooling *> $null
        }
        catch { $missingCaught = $true }
        $missingReport = Get-Content -LiteralPath $summaryJson -Raw | ConvertFrom-Json
        Assert-Equal -Actual $missingCaught -Expected $true -Because "a missing executable cannot inherit the preceding exit zero"
        Assert-Equal -Actual $missingReport.status -Expected "failed" -Because "command launch errors fail the run"
        Assert-Equal -Actual $missingReport.steps[1].reason -Expected "command_error" -Because "command launch failure is distinct from native nonzero exit"
        Assert-Equal -Actual $missingReport.steps[2].status -Expected "not_run" -Because "a missing command also stops later checks"

        function Invoke-ReportFixtureSuccess {
            param([string]$Marker)
            Write-Output $Marker
        }
        $staleCodeProbe = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes('exit 17'))
        & $powershellExecutable -NoProfile -EncodedCommand $staleCodeProbe
        $helperStep = New-Step -Id "report-helper" -Name "PowerShell helper fixture" -WorkingDirectory $fixtureRoot -Command "Invoke-ReportFixtureSuccess" -Arguments @("fixture helper passed")
        $helperResult = Invoke-LoggedStep -Step $helperStep -LogDirectory $fixtureRoot -Number 1 -Total 1 6> $null
        Assert-Equal -Actual $helperResult.status -Expected "passed" -Because "a successful PowerShell helper does not inherit an earlier native exit 17"
        Assert-Equal -Actual $helperResult.exitCode -Expected 0 -Because "a successful helper has its own successful result"

        $priorGlobalExit = Get-Variable -Name LASTEXITCODE -Scope Global -ErrorAction SilentlyContinue
        try {
            Remove-Variable -Name LASTEXITCODE -Scope Global -ErrorAction SilentlyContinue
            $coldStep = New-Step -Id "report-cold" -Name "First command fixture" -WorkingDirectory $fixtureRoot -Command "Invoke-ReportFixtureSuccess" -Arguments @("cold helper passed")
            $coldResult = Invoke-LoggedStep -Step $coldStep -LogDirectory $fixtureRoot -Number 1 -Total 1 6> $null
            Assert-Equal -Actual $coldResult.status -Expected "passed" -Because "strict mode permits execution before any native exit code exists"
            Assert-Equal -Actual ($null -eq (Get-Variable -Name LASTEXITCODE -Scope Global -ErrorAction SilentlyContinue)) -Expected $true -Because "a previously absent global exit code remains absent"
        }
        finally {
            if ($null -eq $priorGlobalExit) {
                Remove-Variable -Name LASTEXITCODE -Scope Global -ErrorAction SilentlyContinue
            }
            else {
                Set-Variable -Name LASTEXITCODE -Scope Global -Value $priorGlobalExit.Value
            }
        }

        function Invoke-ReportFixtureCancelled {
            param([string]$Marker)
            throw (New-Object System.OperationCanceledException($Marker))
        }
        $cancelledStep = New-Step -Id "report-cancelled" -Name "Interrupted fixture" -WorkingDirectory $fixtureRoot -Command "Invoke-ReportFixtureCancelled" -Arguments @($syntheticSecret) -Environment @{ AURUM_VERIFY_ENV_MISSING = "temporary" }
        $cancelledLocation = (Get-Location).Path
        $cancelledEnvironment = [Environment]::GetEnvironmentVariable("AURUM_VERIFY_ENV_MISSING", "Process")
        $cancelledCaught = $false
        try {
            Invoke-VerificationSteps -Steps @($passStep, $cancelledStep, $sentinelStep) -LogDirectory $fixtureRoot -Mode Auto -Risk tooling *> $null
        }
        catch {
            $cancelledCaught = $true
            Assert-Equal -Actual $_.Exception.Message.Contains($syntheticSecret) -Expected $false -Because "cancellation does not echo exception payloads"
        }
        $cancelledReport = Get-Content -LiteralPath $summaryJson -Raw | ConvertFrom-Json
        Assert-Equal -Actual $cancelledCaught -Expected $true -Because "interruption propagates a non-success signal"
        Assert-Equal -Actual $cancelledReport.status -Expected "interrupted" -Because "an interrupted run is not marked passed or complete"
        Assert-Equal -Actual ($cancelledReport.steps.status -join ",") -Expected "passed,interrupted,not_run" -Because "interruption preserves the completed prefix and pending suffix"
        Assert-Equal -Actual $cancelledReport.steps[1].reason -Expected "interrupted" -Because "cancellation uses the allowed interruption reason"
        Assert-Equal -Actual ((Get-Location).Path) -Expected $cancelledLocation -Because "interruption restores the working directory"
        Assert-Equal -Actual ([Environment]::GetEnvironmentVariable("AURUM_VERIFY_ENV_MISSING", "Process")) -Expected $cancelledEnvironment -Because "interruption restores the process environment"
        Assert-Equal -Actual (Test-Path -LiteralPath $sentinelFile) -Expected $false -Because "interruption never runs a subsequent command"
    }

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
    $resolvedFixtureParent = [IO.Path]::GetFullPath($fixtureParent) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolvedFixtureRoot.StartsWith($resolvedFixtureParent, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a fixture directory outside the workspace fixture directory."
    }
    if ([IO.Directory]::Exists($resolvedFixtureRoot)) {
        [IO.File]::Delete((Join-Path $resolvedFixtureRoot "scripts/start-current-main-demo.ps1"))
        [IO.File]::Delete((Join-Path $resolvedFixtureRoot "scripts/start-local-demo-admin.ps1"))
        [IO.File]::Delete((Join-Path $resolvedFixtureRoot ".codex/demo-public-website-url"))
        foreach ($directory in @("scripts", ".codex")) {
            $fixtureDirectory = Join-Path $resolvedFixtureRoot $directory
            if ([IO.Directory]::Exists($fixtureDirectory)) {
                [IO.Directory]::Delete($fixtureDirectory, $false)
            }
        }
        [IO.File]::Delete((Join-Path $resolvedFixtureRoot "good.txt"))
        [IO.File]::Delete((Join-Path $resolvedFixtureRoot "bad.txt"))
        [IO.File]::Delete((Join-Path $resolvedFixtureRoot "01-environment-0.log"))
        [IO.File]::Delete((Join-Path $resolvedFixtureRoot "01-environment-17.log"))
        foreach ($reportFixture in @(
            "summary.json", "summary.md", "unexpected-sentinel.txt",
            "01-report-pass.log", "02-report-second-pass.log", "02-report-fail.log",
            "02-report-missing.log", "01-report-helper.log", "01-report-cold.log", "02-report-cancelled.log",
            "03-report-sentinel.log"
        )) {
            [IO.File]::Delete((Join-Path $resolvedFixtureRoot $reportFixture))
        }
        [IO.Directory]::Delete($resolvedFixtureRoot, $false)
    }
}

& (Join-Path $PSScriptRoot "test-backend-isolated.ps1")
Write-Host "Verification tooling self-test passed: $script:assertions assertions." -ForegroundColor Green
exit 0
