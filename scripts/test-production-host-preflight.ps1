[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$validator = Join-Path $PSScriptRoot "Test-ProductionHostPreflight.ps1"
$generator = Join-Path $PSScriptRoot "New-ProductionSecrets.ps1"
$tlsGenerator = Join-Path $PSScriptRoot "New-ProductionInternalTls.ps1"
$compose = Join-Path (Split-Path $PSScriptRoot -Parent) "docker-compose.production.yml"
$powershellExecutable = (Get-Process -Id $PID).Path
$script:assertions = 0

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

$generatorText = [IO.File]::ReadAllText($generator)
$secretNames = @(
    [regex]::Matches($generatorText, 'Write-Secret -Name "(?<name>[A-Z0-9_]+)"') |
        ForEach-Object { $_.Groups["name"].Value } |
        Sort-Object -Unique
)
$validatorText = [IO.File]::ReadAllText($validator)
$requiredSecretBlock = [regex]::Match(
    $validatorText,
    '(?s)\$requiredSecrets\s*=\s*@\((?<body>.*?)\r?\n\)'
)
if (-not $requiredSecretBlock.Success) {
    throw "Cannot find the required secret contract in the preflight validator."
}
$validatorSecretNames = @(
    [regex]::Matches($requiredSecretBlock.Groups["body"].Value, '"(?<name>[A-Z0-9_]+)"') |
        ForEach-Object { $_.Groups["name"].Value } |
        Sort-Object -Unique
)
$composeSecretNames = @(
    [regex]::Matches(
        [IO.File]::ReadAllText($compose),
        'AURUM_SECRET_FILES_DIR[^}]*\}/(?<name>[A-Z0-9_]+)"'
    ) |
        ForEach-Object { $_.Groups["name"].Value } |
        Sort-Object -Unique
)
Assert-Equal -Actual ($validatorSecretNames -join ",") -Expected ($secretNames -join ",") -Because "the generator and preflight secret contracts match"
Assert-Equal -Actual ($composeSecretNames -join ",") -Expected ($secretNames -join ",") -Because "the generator and production Compose secret contracts match"

function Invoke-Validator {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$CheckUnixPermissions,
        [switch]$CheckBackupMount,
        [switch]$ReportUnexpectedFailure
    )

    $arguments = @("-NoProfile")
    if ($env:OS -eq "Windows_NT") {
        $arguments += @("-ExecutionPolicy", "Bypass")
    }
    $arguments += @("-File", $validator, "-EnvFile", $Path)
    if (-not $CheckBackupMount) {
        $arguments += "-SkipBackupMountCheck"
    }
    if (-not $CheckUnixPermissions) {
        $arguments += "-SkipUnixPermissionCheck"
    }

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = @(& $powershellExecutable @arguments 2>&1)
        $exitCode = $LASTEXITCODE
        if ($ReportUnexpectedFailure -and $exitCode -ne 0) {
            $output | ForEach-Object { Write-Warning "$_" }
        }
        return $exitCode
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Write-TestEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [hashtable]$Overrides = @{}
    )

    $paths = @{}
    foreach ($name in @(
        "secrets", "backup-repository", "backup-scratch", "wal-archive",
        "internal-tls",
        "recovery-metrics", "offsite-secrets", "offsite-restore-secrets",
        "recovery-trust-secrets", "offsite-candidates", "offsite-approvals",
        "verified-checkpoints", "signing-authorizations", "trusted-checkpoints"
    )) {
        $paths[$name] = (New-Item -ItemType Directory -Path (Join-Path $Root $name)).FullName
    }
    foreach ($name in $secretNames) {
        [IO.File]::WriteAllText((Join-Path $paths.secrets $name), "test-value")
    }
    & $powershellExecutable `
        -NoProfile `
        -File $tlsGenerator `
        -OutputDirectory $paths."internal-tls" *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to generate test internal TLS material."
    }
    $workerPassword = "worker-test-password"
    [IO.File]::WriteAllText(
        (Join-Path $paths.secrets "AURUM_WORKER_PASSWORD"),
        $workerPassword
    )
    [IO.File]::WriteAllText(
        (Join-Path $paths.secrets "DATABASE_URL_WORKER"),
        "postgresql+asyncpg://aurum_worker:$workerPassword@postgres:5432/aurum?sslmode=verify-full&sslrootcert=/run/tls/ca.crt"
    )
    foreach ($name in @(
        "DATABASE_URL_APP", "DATABASE_URL_SUPPORT", "DATABASE_URL_MAILER",
        "DATABASE_URL_BILLING_WORKER", "DATABASE_URL_MIGRATION"
    )) {
        [IO.File]::WriteAllText(
            (Join-Path $paths.secrets $name),
            "postgresql+asyncpg://test:test-password@postgres:5432/aurum?sslmode=verify-full&sslrootcert=/run/tls/ca.crt"
        )
    }
    foreach ($name in @("DATABASE_URL_BACKUP", "DATABASE_URL_PITR")) {
        [IO.File]::WriteAllText(
            (Join-Path $paths.secrets $name),
            "postgresql://test:test-password@postgres:5432/aurum?sslmode=verify-full&sslrootcert=/run/tls/ca.crt"
        )
    }
    [IO.File]::WriteAllText(
        (Join-Path $paths.secrets "REDIS_URL"),
        "rediss://:test-password@redis:6379/0?ssl_cert_reqs=required&ssl_check_hostname=true&ssl_ca_certs=/run/tls/ca.crt"
    )

    $settings = [ordered]@{
        AURUM_DOMAIN = "staging.aurum.tj"
        AURUM_PUBLIC_ORIGIN = "https://staging.aurum.tj"
        AURUM_ACME_EMAIL = "ops@aurum.tj"
        AURUM_SECRET_FILES_DIR = $paths.secrets
        AURUM_INTERNAL_TLS_DIR = $paths."internal-tls"
        AURUM_BACKUP_FILESYSTEM_ROOT = $Root
        AURUM_BACKUP_REPOSITORY = $paths."backup-repository"
        AURUM_BACKUP_SCRATCH = $paths."backup-scratch"
        AURUM_WAL_ARCHIVE = $paths."wal-archive"
        AURUM_RECOVERY_METRICS_DIR = $paths."recovery-metrics"
        AURUM_OFFSITE_ENDPOINT = "https://storage.aurum.tj"
        AURUM_OFFSITE_BUCKET = "aurum-test"
        AURUM_OFFSITE_PREFIX = "staging"
        AURUM_OFFSITE_ALLOW_INSECURE = "false"
        AURUM_OFFSITE_SECRET_FILES_DIR = $paths."offsite-secrets"
        AURUM_OFFSITE_RESTORE_SECRET_FILES_DIR = $paths."offsite-restore-secrets"
        AURUM_RECOVERY_TRUST_SECRET_FILES_DIR = $paths."recovery-trust-secrets"
        AURUM_OFFSITE_CANDIDATE_DIR = $paths."offsite-candidates"
        AURUM_OFFSITE_APPROVAL_DIR = $paths."offsite-approvals"
        AURUM_VERIFIED_CHECKPOINT_DIR = $paths."verified-checkpoints"
        AURUM_SIGNING_AUTHORIZATION_DIR = $paths."signing-authorizations"
        AURUM_TRUSTED_CHECKPOINT_DIR = $paths."trusted-checkpoints"
    }
    foreach ($name in $Overrides.Keys) {
        $settings[$name] = $Overrides[$name]
    }

    $envFile = Join-Path $Root "production.env"
    $content = @($settings.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" })
    [IO.File]::WriteAllLines($envFile, $content)
    return @{ EnvFile = $envFile; Paths = $paths }
}

$temporaryDirectory = Join-Path ([IO.Path]::GetTempPath()) (
    "aurum-production-preflight-" + [guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null
try {
    $validRoot = (New-Item -ItemType Directory -Path (Join-Path $temporaryDirectory "valid")).FullName
    $valid = Write-TestEnvironment -Root $validRoot
    Assert-Equal -Actual (Invoke-Validator -Path $valid.EnvFile) -Expected 0 -Because "a complete production configuration passes"
    Remove-Item -LiteralPath (Join-Path $valid.Paths."internal-tls" "ca.key")
    Assert-Equal -Actual (Invoke-Validator -Path $valid.EnvFile) -Expected 0 -Because "the internal CA private key stays off the production host"
    if ([Environment]::OSVersion.Platform -ne "Win32NT") {
        Assert-Equal -Actual (Invoke-Validator -Path $valid.EnvFile -CheckBackupMount) -Expected 2 -Because "a plain directory cannot impersonate the backup filesystem"
        Assert-Equal -Actual (Invoke-Validator -Path $valid.EnvFile -CheckUnixPermissions) -Expected 2 -Because "unsafe Unix secret permissions are rejected"
        & chmod 700 -- $valid.Paths.secrets
        Get-ChildItem -LiteralPath $valid.Paths.secrets -File | ForEach-Object {
            & chmod 600 -- $_.FullName
        }
        & chmod 700 -- $valid.Paths."internal-tls"
        foreach ($name in @("postgres.key", "redis.key", "minio.key")) {
            & chmod 600 -- (Join-Path $valid.Paths."internal-tls" $name)
        }
        Assert-Equal `
            -Actual (
                Invoke-Validator `
                    -Path $valid.EnvFile `
                    -CheckUnixPermissions `
                    -ReportUnexpectedFailure
            ) `
            -Expected 0 `
            -Because "private Unix secret permissions pass"
    }

    $placeholderRoot = (New-Item -ItemType Directory -Path (Join-Path $temporaryDirectory "placeholder")).FullName
    $placeholder = Write-TestEnvironment -Root $placeholderRoot -Overrides @{
        AURUM_DOMAIN = "pharmacy.example.com"
        AURUM_PUBLIC_ORIGIN = "https://pharmacy.example.com"
    }
    Assert-Equal -Actual (Invoke-Validator -Path $placeholder.EnvFile) -Expected 2 -Because "placeholder domains are rejected"

    $originRoot = (New-Item -ItemType Directory -Path (Join-Path $temporaryDirectory "origin")).FullName
    $origin = Write-TestEnvironment -Root $originRoot -Overrides @{
        AURUM_PUBLIC_ORIGIN = "http://staging.aurum.tj"
    }
    Assert-Equal -Actual (Invoke-Validator -Path $origin.EnvFile) -Expected 2 -Because "non-HTTPS origins are rejected"

    $duplicateRoot = (New-Item -ItemType Directory -Path (Join-Path $temporaryDirectory "duplicate")).FullName
    $duplicate = Write-TestEnvironment -Root $duplicateRoot
    Add-Content -LiteralPath $duplicate.EnvFile -Value "AURUM_DOMAIN=duplicate.aurum.tj"
    Assert-Equal -Actual (Invoke-Validator -Path $duplicate.EnvFile) -Expected 2 -Because "duplicate settings are rejected"

    $missingRoot = (New-Item -ItemType Directory -Path (Join-Path $temporaryDirectory "missing")).FullName
    $missing = Write-TestEnvironment -Root $missingRoot
    Remove-Item -LiteralPath (Join-Path $missing.Paths.secrets "JWT_SECRET")
    Assert-Equal -Actual (Invoke-Validator -Path $missing.EnvFile) -Expected 2 -Because "missing secrets are rejected"

    $missingTlsRoot = (New-Item -ItemType Directory -Path (Join-Path $temporaryDirectory "missing-tls")).FullName
    $missingTls = Write-TestEnvironment -Root $missingTlsRoot
    Remove-Item -LiteralPath (Join-Path $missingTls.Paths."internal-tls" "redis.key")
    Assert-Equal -Actual (Invoke-Validator -Path $missingTls.EnvFile) -Expected 2 -Because "missing internal TLS material is rejected"

    $wrongTlsKeyRoot = (New-Item -ItemType Directory -Path (Join-Path $temporaryDirectory "wrong-tls-key")).FullName
    $wrongTlsKey = Write-TestEnvironment -Root $wrongTlsKeyRoot
    Copy-Item `
        -LiteralPath (Join-Path $wrongTlsKey.Paths."internal-tls" "minio.key") `
        -Destination (Join-Path $wrongTlsKey.Paths."internal-tls" "redis.key") `
        -Force
    Assert-Equal -Actual (Invoke-Validator -Path $wrongTlsKey.EnvFile) -Expected 2 -Because "a certificate and private key mismatch is rejected"

    $plainDatabaseRoot = (New-Item -ItemType Directory -Path (Join-Path $temporaryDirectory "plain-database")).FullName
    $plainDatabase = Write-TestEnvironment -Root $plainDatabaseRoot
    [IO.File]::WriteAllText(
        (Join-Path $plainDatabase.Paths.secrets "DATABASE_URL_APP"),
        "postgresql+asyncpg://test:test-password@postgres:5432/aurum"
    )
    Assert-Equal -Actual (Invoke-Validator -Path $plainDatabase.EnvFile) -Expected 2 -Because "plaintext PostgreSQL transport is rejected"

    $plainRedisRoot = (New-Item -ItemType Directory -Path (Join-Path $temporaryDirectory "plain-redis")).FullName
    $plainRedis = Write-TestEnvironment -Root $plainRedisRoot
    [IO.File]::WriteAllText(
        (Join-Path $plainRedis.Paths.secrets "REDIS_URL"),
        "redis://:test-password@redis:6379/0"
    )
    Assert-Equal -Actual (Invoke-Validator -Path $plainRedis.EnvFile) -Expected 2 -Because "plaintext Redis transport is rejected"

    $multilineRoot = (New-Item -ItemType Directory -Path (Join-Path $temporaryDirectory "multiline")).FullName
    $multiline = Write-TestEnvironment -Root $multilineRoot
    [IO.File]::WriteAllText((Join-Path $multiline.Paths.secrets "JWT_SECRET"), "line-one`nline-two")
    Assert-Equal -Actual (Invoke-Validator -Path $multiline.EnvFile) -Expected 2 -Because "multiline secrets are rejected"

    $workerUrlRoot = (New-Item -ItemType Directory -Path (Join-Path $temporaryDirectory "worker-url")).FullName
    $workerUrl = Write-TestEnvironment -Root $workerUrlRoot
    [IO.File]::WriteAllText(
        (Join-Path $workerUrl.Paths.secrets "DATABASE_URL_WORKER"),
        "postgresql+asyncpg://aurum_support:wrong@postgres:5432/aurum"
    )
    Assert-Equal -Actual (Invoke-Validator -Path $workerUrl.EnvFile) -Expected 2 -Because "the system worker cannot reuse support credentials"

    $sharedRoot = (New-Item -ItemType Directory -Path (Join-Path $temporaryDirectory "shared")).FullName
    $shared = Write-TestEnvironment -Root $sharedRoot
    $sharedContent = [IO.File]::ReadAllText($shared.EnvFile).Replace(
        $shared.Paths."backup-scratch",
        $shared.Paths."backup-repository"
    )
    [IO.File]::WriteAllText($shared.EnvFile, $sharedContent)
    Assert-Equal -Actual (Invoke-Validator -Path $shared.EnvFile) -Expected 2 -Because "security-sensitive paths cannot be shared"

    $workspaceRoot = (New-Item -ItemType Directory -Path (Join-Path $temporaryDirectory "workspace")).FullName
    $workspace = Write-TestEnvironment -Root $workspaceRoot
    $workspaceContent = [IO.File]::ReadAllText($workspace.EnvFile).Replace(
        $workspace.Paths."recovery-metrics",
        (Join-Path $PSScriptRoot "unsafe-production-data")
    )
    [IO.File]::WriteAllText($workspace.EnvFile, $workspaceContent)
    Assert-Equal -Actual (Invoke-Validator -Path $workspace.EnvFile) -Expected 2 -Because "production data cannot live in the repository"
}
finally {
    Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
}

Write-Host "Production host preflight tests passed ($script:assertions assertions)."
