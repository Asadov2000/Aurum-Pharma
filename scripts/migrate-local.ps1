$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Title,

        [Parameter(Mandatory = $true)]
        [string[]]$Command
    )

    Write-Host "==> $Title"
    $executable = $Command[0]
    $arguments = @()
    if ($Command.Count -gt 1) {
        $arguments = $Command[1..($Command.Count - 1)]
    }

    & $executable @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Title failed with exit code $LASTEXITCODE"
    }
}

$previousErrorActionPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    $versionTableOutput = & docker compose exec -T postgres psql `
        -U postgres -d aurum -Atc `
        "SELECT pg_catalog.to_regclass('public.alembic_version')" 2>&1
    $versionTableExitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $previousErrorActionPreference
}

if ($versionTableExitCode -ne 0) {
    throw "Cannot inspect the Alembic version table"
}

$versionTableExists = -not [string]::IsNullOrWhiteSpace(
    ($versionTableOutput -join "`n").Trim()
)
$currentOutput = @()
if ($versionTableExists) {
    try {
        $ErrorActionPreference = "Continue"
        $currentOutput = & docker compose exec -T postgres psql `
            -U postgres -d aurum -Atc `
            "SELECT count(*)::text || '|' || COALESCE(min(version_num), '') FROM public.alembic_version" 2>&1
        $currentExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($currentExitCode -ne 0) {
        throw "Cannot read the current Alembic revision"
    }
    $revisionRows = @(
        $currentOutput |
            ForEach-Object { $_.ToString().Trim() } |
            Where-Object { $_ -match "^\d+\|.*$" }
    )
    if ($revisionRows.Count -ne 1) {
        throw "Alembic revision query returned an ambiguous result"
    }
    $revisionParts = $revisionRows[0].Split("|", 2)
    if ($revisionParts[0] -ne "1" -or $revisionParts[1] -notmatch "^\d{4}$") {
        throw "Alembic revision ledger must contain exactly one valid revision"
    }
    $currentRevision = [int]$revisionParts[1]
    if ($currentRevision -lt 1 -or $currentRevision -gt 67) {
        throw "Alembic revision is unknown to this release"
    }
}
else {
    Invoke-Checked "Validating that the database is genuinely empty" @(
        "docker", "compose", "--profile", "maintenance", "run", "--rm", "db-role-bootstrap"
    )
    $currentRevision = 0
}

if ($currentRevision -lt 32) {
    if ($currentRevision -lt 30) {
        Invoke-Checked "Applying support-role migrations through 0029" @(
            "docker", "compose", "--profile", "maintenance", "run", "--rm",
            "migrate", "python", "-m", "app.migrate", "legacy-upgrade", "0029"
        )
    }
    Invoke-Checked "Applying database-owner hardening through revision 0032" @(
        "docker", "compose", "--profile", "maintenance", "run", "--rm", "db-owner-migrate"
    )
}

Invoke-Checked "Bootstrapping separated database roles" @(
    "docker", "compose", "--profile", "maintenance", "run", "--rm", "db-role-bootstrap"
)

Invoke-Checked "Applying remaining migrations with isolated credentials" @(
    "docker", "compose", "--profile", "maintenance", "run", "--rm", "migrate"
)
