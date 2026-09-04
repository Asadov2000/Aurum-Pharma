param(
    [switch]$RunE2E,
    [switch]$SkipComposeUp
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

function Write-Ok {
    param([string]$Message)
    Write-Host "[OK] $Message"
}

function Stop-Smoke {
    param([string]$Message)
    Write-Error $Message
    exit 1
}

function Invoke-Checked {
    param(
        [string]$Title,
        [string[]]$Command
    )

    Write-Step $Title
    $exe = $Command[0]
    $args = @()
    if ($Command.Count -gt 1) {
        $args = $Command[1..($Command.Count - 1)]
    }

    & $exe @args
    if ($LASTEXITCODE -ne 0) {
        Stop-Smoke "$Title failed with exit code $LASTEXITCODE"
    }
}

function Wait-HttpOk {
    param(
        [string]$Name,
        [string]$Url
    )

    $lastError = ""
    for ($attempt = 1; $attempt -le 12; $attempt++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 10
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                Write-Ok "$Name is available ($($response.StatusCode))"
                return
            }
            $lastError = "HTTP $($response.StatusCode)"
        }
        catch {
            $lastError = $_.Exception.Message
        }

        Start-Sleep -Seconds 5
    }

    Stop-Smoke "$Name is not available at $Url. Last error: $lastError"
}

function Invoke-SqlScalar {
    param([string]$Sql)

    $output = & docker compose exec -T postgres psql `
        -U postgres `
        -d aurum `
        -At `
        -v ON_ERROR_STOP=1 `
        -c $Sql

    if ($LASTEXITCODE -ne 0) {
        Stop-Smoke "SQL check failed: $Sql"
    }

    return (($output -join "`n").Trim())
}

function Assert-MinCount {
    param(
        [string]$Name,
        [int]$Actual,
        [int]$Expected,
        [string]$RecoveryHint
    )

    if ($Actual -lt $Expected) {
        Stop-Smoke "${Name}: expected at least $Expected, got $Actual. Recovery: $RecoveryHint"
    }

    Write-Ok "${Name}: $Actual"
}

function Assert-ContainerRunning {
    param([string]$Service)

    $runningServices = & docker compose ps --services --status running
    if ($LASTEXITCODE -ne 0) {
        Stop-Smoke "docker compose ps --services failed"
    }

    if ($runningServices -notcontains $Service) {
        Stop-Smoke "Docker service '$Service' is not running"
    }

    Write-Ok "Docker service '$Service' is running"
}

function Assert-HealthyContainer {
    param([string]$Container)

    $status = (& docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}" $Container).Trim()
    if ($LASTEXITCODE -ne 0) {
        Stop-Smoke "Cannot inspect Docker container '$Container'"
    }

    if ($status -ne "healthy") {
        Stop-Smoke "Docker container '$Container' is not healthy: $status"
    }

    Write-Ok "Docker container '$Container' is healthy"
}

Write-Host "Aurum Pharma local demo smoke check"
Write-Host "Safe mode: no database drop, no alembic downgrade, no forced reseed."

if (-not $SkipComposeUp) {
    Invoke-Checked "Starting Docker Compose stack" @("docker", "compose", "up", "-d")
}

Invoke-Checked "Showing Docker Compose status" @("docker", "compose", "ps")

Write-Step "Checking required Docker services"
$requiredServices = @(
    "postgres",
    "postgres-test",
    "redis",
    "minio",
    "backend",
    "frontend",
    "celery-worker",
    "catalog-worker",
    "billing-worker",
    "platform-mailer",
    "celery-beat",
    "prometheus"
)
foreach ($service in $requiredServices) {
    Assert-ContainerRunning $service
}

Write-Step "Checking healthchecks"
Assert-HealthyContainer "aurum-postgres"
Assert-HealthyContainer "aurum-postgres-test"
Assert-HealthyContainer "aurum-redis"
Assert-HealthyContainer "aurum-minio"

Invoke-Checked "Applying Alembic migrations with database hardening" @(
    "powershell.exe",
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    (Join-Path $RepoRoot "scripts\migrate-local.ps1")
)

Write-Step "Checking HTTP endpoints"
Wait-HttpOk "Backend /healthz" "http://localhost:8000/healthz"
Wait-HttpOk "Backend /docs" "http://localhost:8000/docs"
Wait-HttpOk "Frontend" "http://localhost:5173"

Write-Step "Checking demo seed data"
$baseSeedHint = "base dev seed is missing; do not recreate dev@/admin@/owner@ manually"
$migrationHint = "run scripts/migrate-local.ps1; if it still fails, stop"
$demoSeedHint = "run docker compose exec backend python -m app.seed_demo"

$users = [int](Invoke-SqlScalar "SELECT count(*) FROM app_user WHERE email_lower IN ('dev@aurum.tj','admin@aurum.tj','owner@aurum.tj');")
Assert-MinCount "Seed users" $users 3 $baseSeedHint

$activeOwnerTenant = [int](Invoke-SqlScalar "SELECT count(*) FROM tenant t JOIN app_user u ON u.home_tenant_id = t.id WHERE u.email_lower = 'owner@aurum.tj' AND t.status = 'active';")
Assert-MinCount "Owner active demo tenant" $activeOwnerTenant 1 $baseSeedHint

$roleTemplates = [int](Invoke-SqlScalar "SELECT count(*) FROM role_template WHERE slug IN ('owner','cashier') AND is_active = true;")
Assert-MinCount "Role templates" $roleTemplates 2 $migrationHint

$ownerAssignments = [int](Invoke-SqlScalar "SELECT count(*) FROM user_assignment ua JOIN app_user u ON u.id = ua.user_id WHERE u.email_lower = 'owner@aurum.tj' AND ua.is_active = true;")
Assert-MinCount "Owner role assignment" $ownerAssignments 1 $migrationHint

$branches = [int](Invoke-SqlScalar "SELECT count(*) FROM branch b JOIN app_user u ON u.home_tenant_id = b.tenant_id WHERE u.email_lower = 'owner@aurum.tj' AND b.is_active = true;")
Assert-MinCount "Demo branches" $branches 1 $demoSeedHint

$registers = [int](Invoke-SqlScalar "SELECT count(*) FROM register r JOIN branch b ON b.id = r.branch_id JOIN app_user u ON u.home_tenant_id = b.tenant_id WHERE u.email_lower = 'owner@aurum.tj' AND r.is_active = true;")
Assert-MinCount "Demo registers" $registers 1 $demoSeedHint

$catalogItems = [int](Invoke-SqlScalar "SELECT count(*) FROM tenant_catalog tc JOIN app_user u ON u.home_tenant_id = tc.tenant_id WHERE u.email_lower = 'owner@aurum.tj' AND tc.master_id IS NOT NULL AND tc.deleted_at IS NULL;")
Assert-MinCount "Demo catalog items" $catalogItems 10 $demoSeedHint

$batches = [int](Invoke-SqlScalar "SELECT count(*) FROM batch b JOIN app_user u ON u.home_tenant_id = b.tenant_id WHERE u.email_lower = 'owner@aurum.tj';")
Assert-MinCount "Demo stock batches" $batches 1 $demoSeedHint

$sales = [int](Invoke-SqlScalar "SELECT count(*) FROM sale s JOIN app_user u ON u.home_tenant_id = s.tenant_id WHERE u.email_lower = 'owner@aurum.tj' AND s.status = 'completed';")
Assert-MinCount "Demo completed sales" $sales 1 $demoSeedHint

if ($RunE2E) {
    $playwrightCmd = Join-Path $RepoRoot "frontend\node_modules\.bin\playwright.cmd"
    if (-not (Test-Path $playwrightCmd)) {
        Stop-Smoke "Playwright CLI is not installed on the host. Run: cd frontend; pnpm install; pnpm exec playwright install chromium"
    }

    $isolatedE2E = Join-Path $RepoRoot "scripts\e2e-isolated.ps1"
    Invoke-Checked "Running Playwright E2E in a disposable stack" @(
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $isolatedE2E
    )
}
else {
    Write-Host ""
    Write-Host "E2E skipped. To include browser E2E, run:"
    Write-Host "powershell -ExecutionPolicy Bypass -File .\scripts\demo-smoke.ps1 -RunE2E"
}

Write-Host ""
Write-Host "[OK] Local demo is ready."
