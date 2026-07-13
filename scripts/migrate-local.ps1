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
    $currentOutput = & docker compose exec -T backend alembic current 2>&1
    $currentExitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $previousErrorActionPreference
}

if ($currentExitCode -ne 0) {
    throw "Cannot read the current Alembic revision"
}

$revisionMatches = [regex]::Matches(($currentOutput -join "`n"), "\b(\d{4})\b")
$currentRevision = if ($revisionMatches.Count -gt 0) {
    [int]$revisionMatches[$revisionMatches.Count - 1].Groups[1].Value
}
else {
    0
}

if ($currentRevision -lt 32) {
    if ($currentRevision -lt 30) {
        Invoke-Checked "Applying support-role migrations through 0029" @(
            "docker", "compose", "exec", "-T", "backend", "alembic", "upgrade", "0029"
        )
    }
    Invoke-Checked "Applying database-owner hardening through revision 0032" @(
        "docker", "compose", "--profile", "maintenance", "run", "--rm", "db-owner-migrate"
    )
}

Invoke-Checked "Applying remaining support-role migrations" @(
    "docker", "compose", "exec", "-T", "backend", "alembic", "upgrade", "head"
)
