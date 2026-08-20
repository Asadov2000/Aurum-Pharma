[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$SkipSeed,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$composeFile = Join-Path $workspace "docker-compose.demo.yml"
$seedModule = Join-Path $workspace "backend\app\seed_showcase.py"
$environmentFile = Join-Path $workspace ".env.showcase.local"
$environmentFileName = ".env.showcase.local"
$secretHexLengths = [ordered]@{
    AURUM_DEMO_POSTGRES_PASSWORD = 64
    AURUM_DEMO_APP_PASSWORD = 64
    AURUM_DEMO_SUPPORT_PASSWORD = 64
    AURUM_DEMO_MAILER_PASSWORD = 64
    AURUM_DEMO_BILLING_WORKER_PASSWORD = 64
    AURUM_DEMO_MIGRATOR_PASSWORD = 64
    AURUM_DEMO_REDIS_PASSWORD = 64
    AURUM_DEMO_JWT_SECRET = 96
    AURUM_DEMO_MFA_ENCRYPTION_KEY = 96
    AURUM_DEMO_MINIO_ACCESS_KEY = 32
    AURUM_DEMO_MINIO_SECRET_KEY = 64
}
$credentialBoundVolumes = @(
    "aurum-demo-postgres-data",
    "aurum-demo-redis-data",
    "aurum-demo-minio-data"
)
$showcaseContainers = @(
    "aurum-demo-postgres",
    "aurum-demo-redis",
    "aurum-demo-minio",
    "aurum-demo-backend",
    "aurum-demo-celery-worker",
    "aurum-demo-billing-worker",
    "aurum-demo-platform-mailer",
    "aurum-demo-celery-beat",
    "aurum-demo-frontend"
)
$composeArgs = @(
    "compose",
    "--env-file", $environmentFile,
    "--project-name", "aurum-demo",
    "--file", $composeFile
)

# These containers use the same host ports as the showcase stack. Stopping them
# preserves their containers and volumes; no shared development data is removed.
$conflictingDevContainers = @(
    "aurum-frontend",
    "aurum-backend",
    "aurum-celery-worker",
    "aurum-billing-worker",
    "aurum-celery-beat",
    "aurum-postgres",
    "aurum-redis",
    "aurum-minio"
)

function New-CryptographicHex {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateRange(16, 128)]
        [int]$ByteCount
    )

    $bytes = New-Object byte[] $ByteCount
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }

    return ([System.BitConverter]::ToString($bytes) -replace "-", "").ToLowerInvariant()
}

function Assert-EnvironmentFileIsIgnored {
    $gitDirectory = Join-Path $workspace ".git"
    if (-not (Test-Path -LiteralPath $gitDirectory)) {
        return
    }

    & git -C $workspace check-ignore --quiet -- $environmentFileName
    if ($LASTEXITCODE -ne 0) {
        throw (
            "$environmentFileName is not ignored by Git. " +
            "Refusing to create or use a tracked secret file."
        )
    }
}

function Protect-LocalSecretFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if ($env:OS -eq "Windows_NT") {
        $currentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        $currentSid = $currentIdentity.User
        if ($null -eq $currentSid) {
            throw "Unable to determine the current Windows user for secret-file ACLs"
        }

        & icacls.exe $Path `
            "/inheritance:r" `
            "/grant:r" `
            "*$($currentSid.Value):(F)" `
            "*S-1-5-18:(F)" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to restrict Windows ACLs on $environmentFileName"
        }
        return
    }

    & chmod 600 -- $Path
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to restrict permissions on $environmentFileName"
    }
}

function Assert-ValidShowcaseEnvironmentFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$environmentFileName must be a regular file, not a link or reparse point"
    }

    $values = @{}
    $lineNumber = 0
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        $lineNumber++
        $trimmed = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith("#")) {
            continue
        }
        if ($trimmed -notmatch "^(?<name>[A-Z0-9_]+)=(?<value>[0-9a-f]+)$") {
            throw "$environmentFileName contains an invalid entry at line $lineNumber"
        }

        $name = $Matches["name"]
        $value = $Matches["value"]
        if (-not $secretHexLengths.Contains($name)) {
            throw "$environmentFileName contains an unexpected key: $name"
        }
        if ($values.ContainsKey($name)) {
            throw "$environmentFileName contains a duplicate key: $name"
        }
        if ($value.Length -ne $secretHexLengths[$name]) {
            throw "$environmentFileName contains a value with an invalid length for $name"
        }
        $values[$name] = $value
    }

    foreach ($name in $secretHexLengths.Keys) {
        if (-not $values.ContainsKey($name)) {
            throw "$environmentFileName is missing required key: $name"
        }
    }

    $uniqueValues = @($values.Values | Sort-Object -Unique)
    if ($uniqueValues.Count -ne $secretHexLengths.Count) {
        throw "$environmentFileName must use a distinct value for every secret"
    }
}

function Assert-NoProcessSecretOverrides {
    foreach ($name in $secretHexLengths.Keys) {
        if (Test-Path -LiteralPath "Env:$name") {
            throw (
                "Process environment variable $name would override $environmentFileName. " +
                "Remove the override and run the script again."
            )
        }
    }
}

function Assert-NoOrphanedShowcaseState {
    $existingVolumes = @(& docker volume ls --format "{{.Name}}")
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Docker volumes before creating showcase credentials"
    }

    $existingContainers = @(& docker ps --all --format "{{.Names}}")
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Docker containers before creating showcase credentials"
    }

    $orphanedResources = @(
        $credentialBoundVolumes | Where-Object { $existingVolumes -contains $_ }
        $showcaseContainers | Where-Object { $existingContainers -contains $_ }
    )
    if ($orphanedResources.Count -gt 0) {
        throw (
            "$environmentFileName is missing while persistent showcase state exists. " +
            "Refusing to generate mismatched credentials. Restore the original local " +
            "secret file; no container or volume was changed."
        )
    }
}

function New-ShowcaseEnvironmentFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $fileStream = $null
    try {
        $fileStream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
    }
    finally {
        if ($null -ne $fileStream) {
            $fileStream.Dispose()
        }
    }

    try {
        Protect-LocalSecretFile -Path $Path

        $lines = New-Object System.Collections.Generic.List[string]
        $lines.Add("# Generated locally by scripts/start-showcase-demo.ps1. Do not commit.")
        foreach ($name in $secretHexLengths.Keys) {
            $byteCount = [int]($secretHexLengths[$name] / 2)
            $lines.Add("$name=$(New-CryptographicHex -ByteCount $byteCount)")
        }

        $utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)
        [System.IO.File]::WriteAllLines($Path, $lines, $utf8WithoutBom)
        Protect-LocalSecretFile -Path $Path
    }
    catch {
        if (Test-Path -LiteralPath $Path) {
            Remove-Item -LiteralPath $Path -Force
        }
        throw
    }
}

function Add-MissingShowcaseSecrets {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $existing = [System.IO.File]::ReadAllLines($Path)
    $missing = @(
        "AURUM_DEMO_MIGRATOR_PASSWORD",
        "AURUM_DEMO_MAILER_PASSWORD",
        "AURUM_DEMO_BILLING_WORKER_PASSWORD"
    ) | Where-Object { $key = $_; -not ($existing | Where-Object { $_ -match "^$key=" }) }
    if ($missing.Count -eq 0) { return }
    if ($DryRun) { throw "$environmentFileName needs a one-time secret upgrade" }

    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    foreach ($key in $missing) {
        $value = New-CryptographicHex -ByteCount 32
        [System.IO.File]::AppendAllText(
            $Path,
            [Environment]::NewLine + "$key=$value" + [Environment]::NewLine,
            $utf8NoBom
        )
    }
    Protect-LocalSecretFile -Path $Path
}

function Ensure-ShowcaseEnvironment {
    Assert-EnvironmentFileIsIgnored
    Assert-NoProcessSecretOverrides

    if (Test-Path -LiteralPath $environmentFile) {
        Add-MissingShowcaseSecrets -Path $environmentFile
        Assert-ValidShowcaseEnvironmentFile -Path $environmentFile
        if (-not $DryRun) {
            Protect-LocalSecretFile -Path $environmentFile
        }
        Write-Host "Using protected local showcase credentials from $environmentFileName"
        return
    }

    if ($DryRun) {
        Write-Host ""
        Write-Host "==> Create protected local showcase credentials"
        Write-Host "$environmentFileName would be created; secret values will not be printed."
        return
    }

    Assert-NoOrphanedShowcaseState
    New-ShowcaseEnvironmentFile -Path $environmentFile
    Assert-ValidShowcaseEnvironmentFile -Path $environmentFile
    Write-Host "Created protected local showcase credentials at $environmentFileName"
}

function Invoke-DockerCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Title,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    Write-Host ""
    Write-Host "==> $Title"
    Write-Host "docker $($Arguments -join ' ')"
    if ($DryRun) {
        return
    }

    $previousPreference = $ErrorActionPreference
    $exitCode = 1
    $ErrorActionPreference = "Continue"
    try {
        & docker @Arguments
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        throw "$Title failed with exit code $exitCode"
    }
}

function Invoke-DemoCompose {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Title,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    Invoke-DockerCommand -Title $Title -Arguments ($composeArgs + $Arguments)
}

function Stop-ConflictingDevContainers {
    if ($DryRun) {
        Write-Host ""
        Write-Host "==> Stop known development containers if they are running"
        Write-Host "Only exact aurum-* container names are considered; no container is removed."
        return
    }

    $runningNames = @(& docker ps --format "{{.Names}}")
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect running Docker containers"
    }

    foreach ($containerName in $conflictingDevContainers) {
        if ($runningNames -contains $containerName) {
            Invoke-DockerCommand `
                -Title "Stop conflicting development container $containerName" `
                -Arguments @("stop", "--timeout", "30", $containerName)
        }
    }
}

function Wait-HttpOk {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string]$Url,

        [string]$ExpectedStatus = ""
    )

    Write-Host ""
    Write-Host "==> Wait for $Name"
    if ($DryRun) {
        Write-Host "GET $Url"
        return
    }

    for ($attempt = 1; $attempt -le 90; $attempt++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                if ([string]::IsNullOrWhiteSpace($ExpectedStatus)) {
                    Write-Host "$Name is ready"
                    return
                }

                $payload = $response.Content | ConvertFrom-Json
                if ($payload.status -eq $ExpectedStatus) {
                    Write-Host "$Name is ready"
                    return
                }
            }
        }
        catch {
            # The service may reset connections while its process is starting.
        }
        if ($attempt -lt 90) {
            Start-Sleep -Seconds 2
        }
    }

    throw "$Name did not become ready: $Url"
}

if (-not (Test-Path -LiteralPath $composeFile)) {
    throw "Showcase Compose file not found: $composeFile"
}
if (-not $SkipSeed -and -not $DryRun -and -not (Test-Path -LiteralPath $seedModule)) {
    throw (
        "The showcase seeder is not available yet: $seedModule. " +
        "Use -SkipSeed to validate infrastructure only."
    )
}

Set-Location $workspace

Invoke-DockerCommand -Title "Check Docker" -Arguments @("version")
Ensure-ShowcaseEnvironment
Invoke-DemoCompose `
    -Title "Validate fail-closed showcase configuration" `
    -Arguments @("config", "--quiet")
Stop-ConflictingDevContainers

if (-not $SkipBuild) {
    Invoke-DemoCompose `
        -Title "Build showcase application images" `
        -Arguments @("build", "backend", "frontend")
}

Invoke-DemoCompose `
    -Title "Start isolated showcase infrastructure" `
    -Arguments @("up", "--detach", "postgres", "redis", "minio")

Invoke-DemoCompose `
    -Title "Bootstrap showcase database roles" `
    -Arguments @("run", "--rm", "db-role-bootstrap")

Invoke-DemoCompose `
    -Title "Apply showcase database migrations" `
    -Arguments @("run", "--rm", "migrate")

Invoke-DemoCompose `
    -Title "Start showcase backend" `
    -Arguments @("up", "--detach", "backend")

Wait-HttpOk `
    -Name "Showcase backend" `
    -Url "http://localhost:8000/healthz" `
    -ExpectedStatus "ok"

if (-not $SkipSeed) {
    Invoke-DemoCompose `
        -Title "Seed realistic showcase data" `
        -Arguments @(
            "exec",
            "-T",
            "backend",
            "python",
            "-m",
            "app.seed_showcase",
            "--profile",
            "realistic"
        )
}

Invoke-DemoCompose `
    -Title "Start showcase workers and frontend" `
    -Arguments @("up", "--detach", "celery-worker", "platform-mailer", "celery-beat", "frontend")

Wait-HttpOk -Name "Showcase frontend" -Url "http://localhost:5173"

Invoke-DemoCompose -Title "Show showcase service status" -Arguments @("ps")

Write-Host ""
if ($DryRun) {
    Write-Host "Dry run complete. No file, container, volume, or database was changed."
    return
}

Write-Host "Aurum Pharma showcase is ready."
Write-Host "Frontend:      http://localhost:5173"
Write-Host "API docs:      http://localhost:8000/docs"
Write-Host "MinIO console: http://localhost:9001"
