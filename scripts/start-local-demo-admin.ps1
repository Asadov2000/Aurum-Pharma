param(
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-AurumAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not $DryRun -and -not (Test-AurumAdministrator)) {
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "`"$PSCommandPath`""
    )
    Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -Verb RunAs
    exit 0
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$failed = $false

function Invoke-AurumStep {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Title,

        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    Write-Host ""
    Write-Host "==> $Title"
    Write-Host "$FilePath $($Arguments -join ' ')"

    if ($DryRun) {
        return
    }

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Step failed: $Title. Exit code: $LASTEXITCODE"
    }
}

try {
    Set-Location $projectRoot
    Write-Host "Aurum Pharma local demo launcher"
    Write-Host "Project: $projectRoot"
    Write-Host "Admin:   $(Test-AurumAdministrator)"

    Invoke-AurumStep "Check Docker" "docker" @("version")
    Invoke-AurumStep "Start Docker Compose services" "docker" @("compose", "up", "-d")
    Invoke-AurumStep "Apply database migrations" "docker" @(
        "compose",
        "exec",
        "-T",
        "backend",
        "alembic",
        "upgrade",
        "head"
    )
    Invoke-AurumStep "Seed demo data" "docker" @(
        "compose",
        "exec",
        "-T",
        "backend",
        "python",
        "-m",
        "app.seed_demo"
    )
    Invoke-AurumStep "Run demo smoke check" "powershell.exe" @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        ".\scripts\demo-smoke.ps1"
    )

    Write-Host ""
    Write-Host "Done."
    Write-Host "Frontend: http://localhost:5173"
    Write-Host "API docs: http://localhost:8000/docs"
} catch {
    $failed = $true
    Write-Host ""
    Write-Host "FAILED:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
} finally {
    if (-not $DryRun) {
        Write-Host ""
        Read-Host "Press Enter to close"
    }
}

if ($failed) {
    exit 1
}

exit 0
