[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EnvFile,

    [switch]$SkipUnixPermissionCheck,

    [switch]$SkipBackupMountCheck
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$isWindowsPlatform = [Environment]::OSVersion.Platform -eq "Win32NT"
$comparison = if ($isWindowsPlatform) {
    [StringComparison]::OrdinalIgnoreCase
} else {
    [StringComparison]::Ordinal
}

$requiredSettings = @(
    "AURUM_DOMAIN",
    "AURUM_PUBLIC_ORIGIN",
    "AURUM_ACME_EMAIL",
    "AURUM_SECRET_FILES_DIR",
    "AURUM_BACKUP_FILESYSTEM_ROOT",
    "AURUM_BACKUP_REPOSITORY",
    "AURUM_BACKUP_SCRATCH",
    "AURUM_WAL_ARCHIVE",
    "AURUM_RECOVERY_METRICS_DIR",
    "AURUM_OFFSITE_ENDPOINT",
    "AURUM_OFFSITE_BUCKET",
    "AURUM_OFFSITE_PREFIX",
    "AURUM_OFFSITE_ALLOW_INSECURE",
    "AURUM_OFFSITE_SECRET_FILES_DIR",
    "AURUM_OFFSITE_RESTORE_SECRET_FILES_DIR",
    "AURUM_RECOVERY_TRUST_SECRET_FILES_DIR",
    "AURUM_OFFSITE_CANDIDATE_DIR",
    "AURUM_OFFSITE_APPROVAL_DIR",
    "AURUM_VERIFIED_CHECKPOINT_DIR",
    "AURUM_SIGNING_AUTHORIZATION_DIR",
    "AURUM_TRUSTED_CHECKPOINT_DIR"
)

$requiredSecrets = @(
    "POSTGRES_PASSWORD",
    "AURUM_APP_PASSWORD",
    "AURUM_SUPPORT_PASSWORD",
    "AURUM_MAILER_PASSWORD",
    "AURUM_BILLING_WORKER_PASSWORD",
    "AURUM_WORKER_PASSWORD",
    "AURUM_MIGRATOR_PASSWORD",
    "AURUM_BACKUP_PASSWORD",
    "AURUM_PITR_PASSWORD",
    "DATABASE_URL_APP",
    "DATABASE_URL_SUPPORT",
    "DATABASE_URL_MAILER",
    "DATABASE_URL_BILLING_WORKER",
    "DATABASE_URL_WORKER",
    "DATABASE_URL_MIGRATION",
    "DATABASE_URL_BACKUP",
    "DATABASE_URL_PITR",
    "REDIS_PASSWORD",
    "REDIS_URL",
    "JWT_SECRET",
    "MFA_ENCRYPTION_KEY",
    "MFA_ENCRYPTION_PREVIOUS_KEYS",
    "EMAIL_OUTBOX_ENCRYPTION_KEY",
    "EMAIL_OUTBOX_ENCRYPTION_PREVIOUS_KEYS",
    "METRICS_TOKEN",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "MINIO_BACKUP_ACCESS_KEY",
    "MINIO_BACKUP_SECRET_KEY",
    "RESTIC_PASSWORD",
    "EMAIL_PASSWORD"
)

$externalDirectorySettings = @(
    "AURUM_SECRET_FILES_DIR",
    "AURUM_BACKUP_FILESYSTEM_ROOT",
    "AURUM_BACKUP_REPOSITORY",
    "AURUM_BACKUP_SCRATCH",
    "AURUM_WAL_ARCHIVE",
    "AURUM_RECOVERY_METRICS_DIR",
    "AURUM_OFFSITE_SECRET_FILES_DIR",
    "AURUM_OFFSITE_RESTORE_SECRET_FILES_DIR",
    "AURUM_RECOVERY_TRUST_SECRET_FILES_DIR",
    "AURUM_OFFSITE_CANDIDATE_DIR",
    "AURUM_OFFSITE_APPROVAL_DIR",
    "AURUM_VERIFIED_CHECKPOINT_DIR",
    "AURUM_SIGNING_AUTHORIZATION_DIR",
    "AURUM_TRUSTED_CHECKPOINT_DIR"
)

function Read-EnvironmentFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Production environment file does not exist."
    }

    $result = @{}
    $lineNumber = 0
    foreach ($line in [IO.File]::ReadAllLines([IO.Path]::GetFullPath($Path))) {
        $lineNumber++
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
            continue
        }
        if ($trimmed -notmatch '^(?<name>[A-Z][A-Z0-9_]*)=(?<value>.*)$') {
            throw "Malformed setting at line $lineNumber."
        }

        $name = $Matches.name
        $value = $Matches.value.Trim()
        if ($result.ContainsKey($name)) {
            throw "Duplicate setting: $name."
        }
        if ([string]::IsNullOrWhiteSpace($value)) {
            throw "Setting $name cannot be empty."
        }
        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $result[$name] = $value
    }
    return $result
}

function Assert-ExternalDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Path
    )

    if (-not [IO.Path]::IsPathRooted($Path)) {
        throw "$Name must be an absolute path."
    }

    $fullPath = [IO.Path]::GetFullPath($Path)
    $workspacePrefix = $workspace.TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    ) + [IO.Path]::DirectorySeparatorChar
    if (
        $fullPath.Equals($workspace, $comparison) -or
        $fullPath.StartsWith($workspacePrefix, $comparison)
    ) {
        throw "$Name must be outside the Git checkout."
    }
    if (-not (Test-Path -LiteralPath $fullPath -PathType Container)) {
        throw "$Name directory does not exist."
    }
    return $fullPath
}

function Get-UnixMode {
    param([Parameter(Mandatory = $true)][string]$Path)

    $mode = (& stat -c '%a' -- $Path 2>$null | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or "$mode" -notmatch '^[0-7]{3,4}$') {
        throw "Cannot inspect Unix permissions for a production path."
    }
    return [Convert]::ToInt32("$mode", 8)
}

function Assert-PrivateUnixMode {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if ((Get-UnixMode -Path $Path) -band 63) {
        throw "$Label must not grant permissions to group or other users."
    }
}

function Test-DescendantPath {
    param(
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Child
    )

    $parentPrefix = $Parent.TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    ) + [IO.Path]::DirectorySeparatorChar
    return $Child.StartsWith($parentPrefix, $comparison)
}

function Assert-WorkerDatabaseUrl {
    param(
        [Parameter(Mandatory = $true)][string]$Value,
        [Parameter(Mandatory = $true)][string]$ExpectedPassword
    )

    $uri = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$uri)) {
        throw "DATABASE_URL_WORKER must be an absolute PostgreSQL URL."
    }
    if (
        $uri.Scheme -cne "postgresql+asyncpg" -or
        $uri.Host -cne "postgres" -or
        $uri.Port -ne 5432 -or
        $uri.AbsolutePath -cne "/aurum" -or
        $uri.Query.Length -gt 0 -or
        $uri.Fragment.Length -gt 0
    ) {
        throw "DATABASE_URL_WORKER must target aurum_worker on postgres:5432/aurum."
    }

    $separator = $uri.UserInfo.IndexOf(":", [StringComparison]::Ordinal)
    if ($separator -lt 1) {
        throw "DATABASE_URL_WORKER must contain dedicated credentials."
    }
    $username = [Uri]::UnescapeDataString($uri.UserInfo.Substring(0, $separator))
    $password = [Uri]::UnescapeDataString($uri.UserInfo.Substring($separator + 1))
    if ($username -cne "aurum_worker" -or $password -cne $ExpectedPassword) {
        throw "DATABASE_URL_WORKER credentials must match AURUM_WORKER_PASSWORD."
    }
}

try {
    $settings = Read-EnvironmentFile -Path $EnvFile
    foreach ($name in $requiredSettings) {
        if (-not $settings.ContainsKey($name)) {
            throw "Required setting is missing: $name."
        }
    }

    $domain = $settings.AURUM_DOMAIN.Trim().ToLowerInvariant()
    if (
        [Uri]::CheckHostName($domain) -ne [UriHostNameType]::Dns -or
        $domain -eq "localhost" -or
        $domain.EndsWith(".localhost") -or
        $domain -eq "example.com" -or
        $domain.EndsWith(".example.com")
    ) {
        throw "AURUM_DOMAIN must be a real DNS hostname, not a placeholder."
    }

    $expectedOrigin = "https://$domain"
    if ($settings.AURUM_PUBLIC_ORIGIN.Trim().ToLowerInvariant() -cne $expectedOrigin) {
        throw "AURUM_PUBLIC_ORIGIN must exactly equal https://AURUM_DOMAIN."
    }

    try {
        $acmeAddress = [Net.Mail.MailAddress]::new($settings.AURUM_ACME_EMAIL)
    }
    catch {
        throw "AURUM_ACME_EMAIL must be a valid email address."
    }
    if ($acmeAddress.Address -cne $settings.AURUM_ACME_EMAIL) {
        throw "AURUM_ACME_EMAIL must contain only one plain email address."
    }

    $offsiteEndpoint = $null
    if (
        -not [Uri]::TryCreate($settings.AURUM_OFFSITE_ENDPOINT, [UriKind]::Absolute, [ref]$offsiteEndpoint) -or
        $offsiteEndpoint.Scheme -cne "https" -or
        $offsiteEndpoint.AbsolutePath -cne "/" -or
        $offsiteEndpoint.Query.Length -gt 0 -or
        $offsiteEndpoint.Fragment.Length -gt 0 -or
        $offsiteEndpoint.Host -eq "s3.example.com"
    ) {
        throw "AURUM_OFFSITE_ENDPOINT must be a real HTTPS origin."
    }
    if ($settings.AURUM_OFFSITE_ALLOW_INSECURE -cne "false") {
        throw "AURUM_OFFSITE_ALLOW_INSECURE must remain false."
    }

    $resolvedDirectories = @{}
    foreach ($name in $externalDirectorySettings) {
        $resolved = Assert-ExternalDirectory -Name $name -Path $settings[$name]
        foreach ($existingName in $resolvedDirectories.Keys) {
            if ($resolved.Equals($resolvedDirectories[$existingName], $comparison)) {
                throw "$name and $existingName must use separate directories."
            }
        }
        $resolvedDirectories[$name] = $resolved
    }

    $backupRoot = $resolvedDirectories.AURUM_BACKUP_FILESYSTEM_ROOT
    foreach ($name in @("AURUM_BACKUP_REPOSITORY", "AURUM_BACKUP_SCRATCH", "AURUM_WAL_ARCHIVE")) {
        if (-not (Test-DescendantPath -Parent $backupRoot -Child $resolvedDirectories[$name])) {
            throw "$name must be inside AURUM_BACKUP_FILESYSTEM_ROOT."
        }
    }

    if (-not $SkipBackupMountCheck) {
        if ($isWindowsPlatform) {
            throw "Production backup mount checks require Linux and PowerShell 7."
        }
        $mountTarget = (& findmnt -n -o TARGET --mountpoint $backupRoot 2>$null | Select-Object -First 1)
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace("$mountTarget")) {
            throw "AURUM_BACKUP_FILESYSTEM_ROOT must be a mounted filesystem."
        }
        $resolvedMountTarget = [IO.Path]::GetFullPath("$mountTarget")
        if (-not $resolvedMountTarget.Equals($backupRoot, $comparison)) {
            throw "Backup filesystem mount does not match AURUM_BACKUP_FILESYSTEM_ROOT."
        }
    }

    $secretDirectory = $resolvedDirectories.AURUM_SECRET_FILES_DIR
    $secretDirectoryItem = Get-Item -LiteralPath $secretDirectory -Force
    if (
        $secretDirectoryItem.PSObject.Properties["LinkType"] -and
        $secretDirectoryItem.LinkType
    ) {
        throw "AURUM_SECRET_FILES_DIR cannot be a symbolic link."
    }

    $secretValues = @{}
    foreach ($name in $requiredSecrets) {
        $path = Join-Path $secretDirectory $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Required production secret is missing: $name."
        }
        $item = Get-Item -LiteralPath $path -Force
        if ($item.PSObject.Properties["LinkType"] -and $item.LinkType) {
            throw "Production secret cannot be a symbolic link: $name."
        }
        $value = [IO.File]::ReadAllText($item.FullName)
        if (
            [string]::IsNullOrWhiteSpace($value) -or
            $value.Contains("`r") -or
            $value.Contains("`n")
        ) {
            throw "Production secret must contain exactly one non-empty line: $name."
        }
        $secretValues[$name] = $value
    }

    Assert-WorkerDatabaseUrl `
        -Value $secretValues.DATABASE_URL_WORKER `
        -ExpectedPassword $secretValues.AURUM_WORKER_PASSWORD

    if (-not $SkipUnixPermissionCheck) {
        if ($isWindowsPlatform) {
            throw "Production host permission checks require Linux and PowerShell 7."
        }
        Assert-PrivateUnixMode -Path $secretDirectory -Label "Production secret directory"
        foreach ($name in $requiredSecrets) {
            Assert-PrivateUnixMode -Path (Join-Path $secretDirectory $name) -Label "Production secret $name"
        }
    }

    Write-Host "Production host preflight passed. No secret values were displayed."
    exit 0
}
catch {
    [Console]::Error.WriteLine(
        "Production host preflight failed: $($_.Exception.Message)"
    )
    exit 2
}
