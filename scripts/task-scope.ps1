[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$BaseRef = "origin/main",
    [string[]]$ChangedPath = @(),
    [ValidateSet("Text", "Json")]
    [string]$Format = "Text"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$useProvidedPaths = $PSBoundParameters.ContainsKey("ChangedPath")

function ConvertTo-RepoPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $normalized = $Path.Trim().Replace("\", "/")
    while ($normalized.StartsWith("./", [StringComparison]::Ordinal)) {
        $normalized = $normalized.Substring(2)
    }
    return $normalized
}

function Invoke-GitLines {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & git @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    if ($exitCode -ne 0) {
        $message = (@($output) -join [Environment]::NewLine).Trim()
        throw "git $($Arguments -join ' ') failed: $message"
    }

    return @($output | ForEach-Object { "$_" } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
}

function Test-AnyPattern {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$Patterns
    )

    foreach ($pattern in $Patterns) {
        if ($Path -match $pattern) {
            return $true
        }
    }
    return $false
}

function Add-UniqueValue {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.Generic.List[string]]$List,
        [Parameter(Mandatory = $true)][string]$Value
    )

    if (-not $List.Contains($Value)) {
        $List.Add($Value)
    }
}

function Get-BackendTestTarget {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ($Path -match '^backend/tests/(.+)$') {
        $candidate = "tests/$($Matches[1])"
        $absolute = Join-Path (Join-Path $repoRoot "backend") $candidate
        $leaf = Split-Path -Leaf $candidate
        if ((Test-Path -LiteralPath $absolute) -and $leaf -match '^test_.+\.py$') {
            return $candidate
        }
        $parent = (Split-Path -Parent $candidate).Replace("\", "/")
        if ([string]::IsNullOrWhiteSpace($parent)) {
            return "tests"
        }
        return $parent
    }
    if ($Path -match '^backend/app/domains/([^/]+)/') {
        $candidate = "tests/domains/$($Matches[1])"
        if (Test-Path -LiteralPath (Join-Path (Join-Path $repoRoot "backend") $candidate)) {
            return $candidate
        }
    }
    if ($Path -match '^backend/app/(core|middleware)/') {
        return "tests/core"
    }
    if ($Path -match '^backend/app/showcase/') {
        return "tests/showcase"
    }
    if ($Path -match '^backend/app/tasks/(billing|auth|roles|platform_accounts)') {
        $candidate = "tests/domains/$($Matches[1])"
        if (Test-Path -LiteralPath (Join-Path (Join-Path $repoRoot "backend") $candidate)) {
            return $candidate
        }
    }
    return "tests"
}

function Get-FrontendTestTarget {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ($Path -match '^frontend/tests/(.+)$') {
        $candidate = "tests/$($Matches[1])"
        $absolute = Join-Path (Join-Path $repoRoot "frontend") $candidate
        $leaf = Split-Path -Leaf $candidate
        if ((Test-Path -LiteralPath $absolute) -and $leaf -match '\.test\.(?:ts|tsx|js|jsx)$') {
            return $candidate
        }
        $parent = (Split-Path -Parent $candidate).Replace("\", "/")
        if ([string]::IsNullOrWhiteSpace($parent)) {
            return "tests"
        }
        return $parent
    }
    if ($Path -match '^frontend/src/features/([^/]+)/') {
        $candidate = "tests/features/$($Matches[1])"
        if (Test-Path -LiteralPath (Join-Path (Join-Path $repoRoot "frontend") $candidate)) {
            return $candidate
        }
    }
    if ($Path -match '^frontend/src/components/(ui|layout)/') {
        return "tests/components/$($Matches[1])"
    }
    if ($Path -match '^frontend/src/lib/') {
        return "tests/lib"
    }
    if ($Path -match '^frontend/src/stores/auth\.ts$') {
        return "tests/features/auth"
    }
    return "tests"
}

function Get-E2ESpecsForPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ($Path -match '^frontend/e2e/(.+\.spec\.ts)$') {
        return @("e2e/$($Matches[1])")
    }

    $groups = @(
        @('(^|/)(pos|sales)(/|$)', @("e2e/pos-sale.spec.ts", "e2e/shift-close-z-report.spec.ts", "e2e/payment-settings.spec.ts")),
        @('(^|/)(auth)(/|$)|stores/auth\.ts$', @("e2e/auth.spec.ts", "e2e/user-session-revocation.spec.ts", "e2e/runtime-surface.spec.ts")),
        @('(^|/)(roles)(/|$)', @("e2e/tenant-setup.spec.ts", "e2e/user-session-revocation.spec.ts")),
        @('(^|/)(billing|platformBilling)(/|$)|tasks/billing', @("e2e/platform-billing.spec.ts")),
        @('(^|/)(incoming|inventory|suppliers)(/|$)', @("e2e/incoming-flow.spec.ts", "e2e/catalog-flow.spec.ts")),
        @('(^|/)(catalog)(/|$)', @("e2e/catalog-flow.spec.ts", "e2e/catalog-import.spec.ts")),
        @('(^|/)(foundation|onboarding)(/|$)', @("e2e/owner-onboarding.spec.ts", "e2e/tenant-setup.spec.ts", "e2e/payment-settings.spec.ts")),
        @('(^|/)(sync|syncCenter)(/|$)', @("e2e/sync-center.spec.ts", "e2e/runtime-surface.spec.ts")),
        @('(^|/)(platform_accounts|platformAccounts)(/|$)', @("e2e/platform-account-lifecycle.spec.ts", "e2e/platform-account-activation.spec.ts")),
        @('(^|/)(platform_access|platformAccess)(/|$)', @("e2e/sync-center.spec.ts", "e2e/support-access.spec.ts")),
        @('(^|/)(support_access|supportAccess)(/|$)', @("e2e/support-access.spec.ts")),
        @('(^|/)(reports|dashboard)(/|$)', @("e2e/reports-export.spec.ts"))
    )

    foreach ($group in $groups) {
        if ($Path -match $group[0]) {
            return @($group[1])
        }
    }
    return @()
}

Push-Location $repoRoot
try {
    if ($useProvidedPaths) {
        $paths = @($ChangedPath)
    }
    else {
        Invoke-GitLines -Arguments @("rev-parse", "--verify", "$BaseRef^{commit}") | Out-Null
        $paths = @(
            Invoke-GitLines -Arguments @("diff", "--no-renames", "--name-only", "--diff-filter=ACDMRTUXB", "$BaseRef...HEAD", "--")
            Invoke-GitLines -Arguments @("diff", "--no-renames", "--name-only", "--diff-filter=ACDMRTUXB", "--")
            Invoke-GitLines -Arguments @("diff", "--cached", "--no-renames", "--name-only", "--diff-filter=ACDMRTUXB", "--")
            Invoke-GitLines -Arguments @("ls-files", "--others", "--exclude-standard")
        )
    }
}
finally {
    Pop-Location
}

$paths = @(
    $paths |
        ForEach-Object { ConvertTo-RepoPath -Path "$_" } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Sort-Object -Unique
)

$documentationPatterns = @(
    '^docs/',
    '(^|/)(AGENTS|README|CONTRIBUTING|SECURITY)\.md$',
    '\.md$'
)
$toolingPatterns = @(
    '^scripts/task-scope\.ps1$',
    '^scripts/verify-change\.ps1$',
    '^scripts/test-verification-tools\.ps1$',
    '^scripts/check-untracked-whitespace\.ps1$'
)
$infrastructurePatterns = @(
    '^\.github/',
    '^infra/',
    '^docker-compose(?:\.[^/]+)?\.ya?ml$',
    '(^|/)Dockerfile(?:\.[^/]+)?$',
    '^frontend/Caddyfile$',
    '^\.env(?:\.[^/]+)?\.example$',
    '^scripts/(?!task-scope|verify-change|test-verification-tools|check-untracked-whitespace).+\.ps1$'
)
$dependencyPatterns = @(
    '^backend/(?:pyproject\.toml|poetry\.lock)$',
    '^frontend/(?:package\.json|pnpm-lock\.yaml)$'
)
$migrationPatterns = @(
    '^backend/alembic/',
    '^backend/alembic\.ini$',
    '^backend/app/(?:migrate|db_owner_migrate)\.py$'
)
$criticalDomainPatterns = @(
    '^backend/(?:app|tests)/domains/(?:auth|roles|platform_access|support_access|platform_accounts|billing|pos|sync|foundation)/',
    '^backend/app/tasks/(?:billing|auth|roles|platform_accounts)',
    '^frontend/(?:src|tests)/features/(?:auth|roles|platformAccess|supportAccess|platformAccounts|billing|platformBilling|pos|sales|syncCenter|foundation)/'
)
$integratedDomainPatterns = @(
    '^backend/(?:app|tests)/domains/(?:catalog|incoming|inventory|suppliers)/',
    '^frontend/(?:src|tests)/features/(?:catalog|incoming|inventory|suppliers)/'
)
$criticalCorePatterns = @(
    '^backend/app/core/(?:security|deps|db|config|redis|billing_worker_db|billing_worker_config|mailer_db|mailer_config)\.py$',
    '^backend/app/maintenance/rotate_mfa_key\.py$',
    '^backend/app/middleware/',
    '^backend/app/main\.py$',
    '^backend/tests/isolation/',
    '^frontend/src/(?:router|main)\.tsx$',
    '^frontend/src/lib/api\.ts$',
    '^frontend/src/stores/(?:auth|supportAccess)\.ts$',
    '^frontend/src/components/layout/(?:RouteAccessGuard|routeAccess|RootLayout|AppLayout)'
)
$sharedUiPatterns = @(
    '^frontend/src/components/(?:ui|layout)/',
    '^frontend/src/styles/'
)
$runtimeSurfacePatterns = @(
    '^frontend/src/lib/(?:pwa|runtime|serverHealth|desktopBridge)\.ts$'
)
$e2eFrameworkPatterns = @(
    '^frontend/e2e/(?:helpers|global-setup|docker)\.ts$',
    '^frontend/playwright\.config\.ts$',
    '^scripts/e2e-isolated\.ps1$'
)
$buildPatterns = @(
    '^frontend/(?:package\.json|pnpm-lock\.yaml|vite\.config\.|tsconfig|index\.html|Dockerfile)',
    '^frontend/src/(?:main|router)\.tsx$',
    '^frontend/src/styles/',
    '^frontend/public/'
)
$knownBackendPatterns = @(
    '^backend/app/domains/(?:audit|auth|billing|catalog|dashboard|foundation|incoming|inventory|notifications|onboarding|platform_access|platform_accounts|pos|roles|suppliers|support_access|sync)/',
    '^backend/app/(?:core|middleware|tasks|maintenance|showcase)/',
    '^backend/app/(?:__init__|main|validate_showcase|seed_showcase|seed_e2e|seed_demo_data|seed_demo|migrate|db_owner_migrate)\.py$',
    '^backend/tests/domains/(?:audit|auth|billing|catalog|dashboard|foundation|incoming|inventory|notifications|onboarding|platform_access|platform_accounts|pos|roles|security|suppliers|support_access|sync)/',
    '^backend/tests/(?:core|isolation|middleware|showcase)/',
    '^backend/tests/(?:__init__|conftest|auth_helpers|platform_access_helpers|test_platform_invitation_email)\.py$',
    '^backend/alembic/',
    '^backend/(?:alembic\.ini|pyproject\.toml|poetry\.lock|Dockerfile(?:\.[^/]+)?)$'
)
$knownFrontendPatterns = @(
    '^frontend/src/features/(?:audit|auth|billing|catalog|dashboard|foundation|incoming|inventory|notifications|onboarding|platformAccess|platformAccounts|platformBilling|pos|reports|roles|sales|suppliers|supportAccess|syncCenter)/',
    '^frontend/src/(?:components|lib|stores|styles)/',
    '^frontend/src/(?:main|router)\.tsx$',
    '^frontend/tests/features/(?:audit|auth|billing|catalog|dashboard|foundation|incoming|inventory|notifications|onboarding|platformAccess|platformAccounts|platformBilling|pos|reports|roles|sales|suppliers|supportAccess|syncCenter)/',
    '^frontend/tests/(?:components|lib)/',
    '^frontend/tests/(?:setup|features/managementSearchApi\.test)\.ts$',
    '^frontend/e2e/',
    '^frontend/public/',
    '^frontend/(?:package\.json|pnpm-lock\.yaml|vite\.config\.ts|playwright\.config\.ts|eslint\.config\.js|tsconfig\.json|index\.html|Caddyfile|Dockerfile(?:\.[^/]+)?)$'
)

$areas = [System.Collections.Generic.List[string]]::new()
$reasons = [System.Collections.Generic.List[string]]::new()
$backendTargets = [System.Collections.Generic.List[string]]::new()
$frontendTargets = [System.Collections.Generic.List[string]]::new()
$e2eSpecs = [System.Collections.Generic.List[string]]::new()

$hasBackend = $false
$hasFrontend = $false
$hasFrontendApp = $false
$hasE2E = $false
$hasInfrastructure = $false
$hasDependency = $false
$hasMigration = $false
$hasCriticalDomain = $false
$hasCriticalCore = $false
$hasIntegratedDomain = $false
$hasSharedUi = $false
$hasRuntimeSurface = $false
$hasE2EFramework = $false
$hasTooling = $false
$hasDocumentation = $false
$hasUnknown = $false
$requiresFrontendBuild = $false

foreach ($path in $paths) {
    if ($path -match '^backend/') {
        $hasBackend = $true
        Add-UniqueValue -List $areas -Value "backend"
        Add-UniqueValue -List $backendTargets -Value (Get-BackendTestTarget -Path $path)

        if ($path -match '^backend/(?:app|tests)/domains/audit/') {
            Add-UniqueValue -List $backendTargets -Value "tests/isolation/test_audit_immutability.py"
        }
        if ($path -match '^backend/(?:app|tests)/domains/catalog/') {
            Add-UniqueValue -List $backendTargets -Value "tests/isolation/test_catalog_rls.py"
        }
        if ($path -match '^backend/(?:app|tests)/domains/incoming/') {
            Add-UniqueValue -List $backendTargets -Value "tests/domains/inventory"
            Add-UniqueValue -List $backendTargets -Value "tests/isolation/test_suppliers_incoming_rls.py"
            Add-UniqueValue -List $backendTargets -Value "tests/isolation/test_supplier_return_immutability.py"
        }
        if ($path -match '^backend/(?:app|tests)/domains/inventory/') {
            Add-UniqueValue -List $backendTargets -Value "tests/isolation/test_inventory_rls.py"
            Add-UniqueValue -List $backendTargets -Value "tests/isolation/test_batch_movement_immutability.py"
            Add-UniqueValue -List $backendTargets -Value "tests/domains/pos/test_atomic_sale.py"
        }
        if ($path -match '^backend/(?:app|tests)/domains/suppliers/') {
            Add-UniqueValue -List $backendTargets -Value "tests/isolation/test_suppliers_incoming_rls.py"
        }
    }
    if ($path -match '^frontend/') {
        $hasFrontend = $true
        Add-UniqueValue -List $areas -Value "frontend"
        if ($path -notmatch '^frontend/e2e/') {
            $hasFrontendApp = $true
            Add-UniqueValue -List $frontendTargets -Value (Get-FrontendTestTarget -Path $path)
        }
    }
    if ($path -match '^frontend/e2e/') {
        $hasE2E = $true
    }
    if (Test-AnyPattern -Path $path -Patterns $documentationPatterns) {
        $hasDocumentation = $true
        Add-UniqueValue -List $areas -Value "docs"
    }
    if (Test-AnyPattern -Path $path -Patterns $toolingPatterns) {
        $hasTooling = $true
        Add-UniqueValue -List $areas -Value "tooling"
    }
    if (Test-AnyPattern -Path $path -Patterns $infrastructurePatterns) {
        $hasInfrastructure = $true
        Add-UniqueValue -List $areas -Value "infrastructure"
    }
    if (Test-AnyPattern -Path $path -Patterns $dependencyPatterns) {
        $hasDependency = $true
        Add-UniqueValue -List $areas -Value "dependencies"
    }
    if (Test-AnyPattern -Path $path -Patterns $migrationPatterns) {
        $hasMigration = $true
        Add-UniqueValue -List $areas -Value "database"
    }
    if (Test-AnyPattern -Path $path -Patterns $criticalDomainPatterns) {
        $hasCriticalDomain = $true
    }
    if (Test-AnyPattern -Path $path -Patterns $criticalCorePatterns) {
        $hasCriticalCore = $true
    }
    if (Test-AnyPattern -Path $path -Patterns $integratedDomainPatterns) {
        $hasIntegratedDomain = $true
    }
    if (Test-AnyPattern -Path $path -Patterns $sharedUiPatterns) {
        $hasSharedUi = $true
    }
    if (Test-AnyPattern -Path $path -Patterns $runtimeSurfacePatterns) {
        $hasRuntimeSurface = $true
        foreach ($spec in @("e2e/pwa.spec.ts", "e2e/runtime-surface.spec.ts", "e2e/startup-performance.spec.ts")) {
            Add-UniqueValue -List $e2eSpecs -Value $spec
        }
    }
    if (Test-AnyPattern -Path $path -Patterns $e2eFrameworkPatterns) {
        $hasE2EFramework = $true
    }
    if (Test-AnyPattern -Path $path -Patterns $buildPatterns) {
        $requiresFrontendBuild = $true
    }
    foreach ($spec in (Get-E2ESpecsForPath -Path $path)) {
        $absoluteSpec = Join-Path (Join-Path $repoRoot "frontend") $spec
        if (-not (Test-Path -LiteralPath $absoluteSpec)) {
            throw "Mapped E2E spec does not exist: $spec"
        }
        Add-UniqueValue -List $e2eSpecs -Value $spec
    }

    $knownPath = (Test-AnyPattern -Path $path -Patterns $documentationPatterns) -or
        (Test-AnyPattern -Path $path -Patterns $toolingPatterns) -or
        (Test-AnyPattern -Path $path -Patterns $infrastructurePatterns) -or
        (Test-AnyPattern -Path $path -Patterns $dependencyPatterns) -or
        (Test-AnyPattern -Path $path -Patterns $knownBackendPatterns) -or
        (Test-AnyPattern -Path $path -Patterns $knownFrontendPatterns)
    if (-not $knownPath) {
        $hasUnknown = $true
        Add-UniqueValue -List $areas -Value "unknown"
    }
}

$docsOnly = $paths.Count -gt 0 -and @($paths | Where-Object {
        -not (Test-AnyPattern -Path $_ -Patterns $documentationPatterns)
    }).Count -eq 0
$toolingOnly = $paths.Count -gt 0 -and @($paths | Where-Object {
        -not ((Test-AnyPattern -Path $_ -Patterns $toolingPatterns) -or
            (Test-AnyPattern -Path $_ -Patterns $documentationPatterns))
    }).Count -eq 0 -and $hasTooling
$crossStack = $hasBackend -and $hasFrontendApp

$risk = "standard"
if ($paths.Count -eq 0) {
    $risk = "none"
}
elseif ($docsOnly) {
    $risk = "docs"
}
elseif ($hasInfrastructure -or $hasDependency -or $hasUnknown -or $hasE2EFramework) {
    $risk = "full"
    if ($hasInfrastructure) {
        Add-UniqueValue -List $reasons -Value "infrastructure"
    }
    if ($hasDependency) {
        Add-UniqueValue -List $reasons -Value "dependency-lock"
    }
    if ($hasUnknown) {
        Add-UniqueValue -List $reasons -Value "unknown-path"
    }
    if ($hasE2EFramework) {
        Add-UniqueValue -List $reasons -Value "e2e-framework"
    }
}
elseif ($hasMigration -or $hasCriticalDomain -or $hasCriticalCore -or $crossStack) {
    $risk = "critical"
    if ($hasMigration) {
        Add-UniqueValue -List $reasons -Value "migration"
    }
    if ($hasCriticalDomain) {
        Add-UniqueValue -List $reasons -Value "critical-domain"
    }
    if ($hasCriticalCore) {
        Add-UniqueValue -List $reasons -Value "shared-security-surface"
    }
    if ($crossStack) {
        Add-UniqueValue -List $reasons -Value "cross-stack"
    }
}
elseif ($hasIntegratedDomain -or $hasSharedUi -or $hasRuntimeSurface) {
    $risk = "integrated"
    if ($hasIntegratedDomain) {
        Add-UniqueValue -List $reasons -Value "stock-domain"
    }
    if ($hasSharedUi) {
        Add-UniqueValue -List $reasons -Value "shared-ui"
        foreach ($spec in @("e2e/interface-layout.spec.ts", "e2e/configurable-filters.spec.ts")) {
            $absoluteSpec = Join-Path (Join-Path $repoRoot "frontend") $spec
            if (-not (Test-Path -LiteralPath $absoluteSpec)) {
                throw "Mapped E2E spec does not exist: $spec"
            }
            Add-UniqueValue -List $e2eSpecs -Value $spec
        }
    }
    if ($hasRuntimeSurface) {
        Add-UniqueValue -List $reasons -Value "runtime-surface"
    }
}
elseif ($toolingOnly) {
    $risk = "tooling"
}

$backendMode = "none"
$frontendMode = "none"
$e2eMode = "none"
$runBackendQuality = $false
$runFrontendQuality = $false
$runToolingSelfTest = $hasTooling
$requiresAllE2E = $risk -eq "full" -or $risk -eq "critical"

if ($risk -eq "full" -or $risk -eq "critical") {
    $backendMode = "full"
    $frontendMode = "full"
    $runBackendQuality = $true
    $runFrontendQuality = $true
    $requiresFrontendBuild = $true
    if ($requiresAllE2E -or $e2eSpecs.Count -eq 0) {
        $e2eMode = "full"
    }
    else {
        $e2eMode = "targeted"
    }
}
elseif ($risk -eq "integrated") {
    if ($hasBackend) {
        $backendMode = "targeted"
        $runBackendQuality = $true
    }
    if ($hasFrontendApp) {
        if ($hasSharedUi) {
            $frontendMode = "full"
        }
        else {
            $frontendMode = "targeted"
        }
        $runFrontendQuality = $true
    }
    $requiresFrontendBuild = $requiresFrontendBuild -or $hasSharedUi
    if ($e2eSpecs.Count -gt 0) {
        $e2eMode = "targeted"
    }
}
elseif ($risk -eq "standard") {
    if ($hasBackend) {
        $backendMode = "targeted"
        $runBackendQuality = $true
    }
    if ($hasFrontendApp) {
        $frontendMode = "targeted"
        $runFrontendQuality = $true
    }
    if ($hasE2E -or $e2eSpecs.Count -gt 0) {
        $e2eMode = "targeted"
    }
}

$result = [ordered]@{
    version = 1
    baseRef = $BaseRef
    paths = @($paths)
    areas = @($areas | Sort-Object)
    risk = $risk
    reasons = @($reasons | Sort-Object)
    flags = [ordered]@{
        backend = $hasBackend
        frontend = $hasFrontend
        e2e = $hasE2E
        tooling = $hasTooling
        documentation = $hasDocumentation
        critical = ($risk -eq "critical" -or $risk -eq "full")
    }
    targets = [ordered]@{
        backend = @($backendTargets | Sort-Object)
        frontend = @($frontendTargets | Sort-Object)
        e2e = @($e2eSpecs | Sort-Object)
    }
    verification = [ordered]@{
        diffCheck = $true
        toolingSelfTest = $runToolingSelfTest
        backendTests = $backendMode
        backendQuality = $runBackendQuality
        frontendTests = $frontendMode
        frontendQuality = $runFrontendQuality
        frontendBuild = $requiresFrontendBuild
        e2e = $e2eMode
    }
}

if ($Format -eq "Json") {
    $result | ConvertTo-Json -Depth 6
    return
}

Write-Host "Aurum change scope"
Write-Host "  Risk:  $risk"
Write-Host "  Paths: $($paths.Count)"
Write-Host "  Areas: $((@($result.areas) -join ', '))"
if ($reasons.Count -gt 0) {
    Write-Host "  Why:   $((@($result.reasons) -join ', '))"
}
Write-Host "Verification"
Write-Host "  Backend tests:  $backendMode"
Write-Host "  Frontend tests: $frontendMode"
Write-Host "  Frontend build: $requiresFrontendBuild"
Write-Host "  E2E:            $e2eMode"
if ($paths.Count -gt 0) {
    Write-Host "Changed paths"
    $displayPaths = @($paths | Select-Object -First 20)
    foreach ($path in $displayPaths) {
        Write-Host "  - $path"
    }
    if ($paths.Count -gt $displayPaths.Count) {
        Write-Host "  ... and $($paths.Count - $displayPaths.Count) more"
    }
}
