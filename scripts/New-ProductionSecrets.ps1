[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [Parameter(Mandatory = $true)]
    [Security.SecureString]$EmailPassword
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$output = [IO.Path]::GetFullPath($OutputDirectory)
$comparison = if ([Environment]::OSVersion.Platform -eq "Win32NT") {
    [StringComparison]::OrdinalIgnoreCase
} else {
    [StringComparison]::Ordinal
}
$workspacePrefix = $workspace.TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
) + [IO.Path]::DirectorySeparatorChar

if (
    $output.Equals($workspace, $comparison) -or
    $output.StartsWith($workspacePrefix, $comparison)
) {
    throw "Production secrets must be stored outside the repository."
}

if (Test-Path -LiteralPath $output) {
    if (@(Get-ChildItem -LiteralPath $output -Force).Count -gt 0) {
        throw "The output directory must be empty. Existing secrets are never overwritten."
    }
} else {
    New-Item -ItemType Directory -Path $output | Out-Null
}

function New-RandomHex {
    param([Parameter(Mandatory = $true)][int]$Bytes)

    $buffer = [byte[]]::new($Bytes)
    [Security.Cryptography.RandomNumberGenerator]::Fill($buffer)
    return [Convert]::ToHexString($buffer).ToLowerInvariant()
}

$utf8NoBom = [Text.UTF8Encoding]::new($false)

function Write-Secret {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    if ([string]::IsNullOrWhiteSpace($Value) -or $Value.Contains("`n") -or $Value.Contains("`r")) {
        throw "Secret $Name must be one non-empty line."
    }
    [IO.File]::WriteAllText((Join-Path $output $Name), $Value, $utf8NoBom)
}

$postgresPassword = New-RandomHex -Bytes 32
$appPassword = New-RandomHex -Bytes 32
$supportPassword = New-RandomHex -Bytes 32
$migratorPassword = New-RandomHex -Bytes 32
$redisPassword = New-RandomHex -Bytes 32
$minioRootUser = New-RandomHex -Bytes 10
$minioRootPassword = New-RandomHex -Bytes 32
$minioAccessKey = New-RandomHex -Bytes 10
$minioSecretKey = New-RandomHex -Bytes 32

$emailPasswordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($EmailPassword)
try {
    $emailPasswordPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
        $emailPasswordPointer
    )
    Write-Secret -Name "EMAIL_PASSWORD" -Value $emailPasswordPlain
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($emailPasswordPointer)
    $emailPasswordPlain = $null
}

Write-Secret -Name "POSTGRES_PASSWORD" -Value $postgresPassword
Write-Secret -Name "AURUM_APP_PASSWORD" -Value $appPassword
Write-Secret -Name "AURUM_SUPPORT_PASSWORD" -Value $supportPassword
Write-Secret -Name "AURUM_MIGRATOR_PASSWORD" -Value $migratorPassword
Write-Secret -Name "DATABASE_URL_APP" -Value (
    "postgresql+asyncpg://aurum_app:{0}@postgres:5432/aurum" -f $appPassword
)
Write-Secret -Name "DATABASE_URL_SUPPORT" -Value (
    "postgresql+asyncpg://aurum_support:{0}@postgres:5432/aurum" -f $supportPassword
)
Write-Secret -Name "DATABASE_URL_MIGRATION" -Value (
    "postgresql+asyncpg://aurum_migrator:{0}@postgres:5432/aurum" -f $migratorPassword
)
Write-Secret -Name "REDIS_PASSWORD" -Value $redisPassword
Write-Secret -Name "REDIS_URL" -Value ("redis://:{0}@redis:6379/0" -f $redisPassword)
Write-Secret -Name "JWT_SECRET" -Value (New-RandomHex -Bytes 48)
Write-Secret -Name "MFA_ENCRYPTION_KEY" -Value (New-RandomHex -Bytes 48)
Write-Secret -Name "MFA_ENCRYPTION_PREVIOUS_KEYS" -Value "{}"
Write-Secret -Name "EMAIL_OUTBOX_ENCRYPTION_KEY" -Value (New-RandomHex -Bytes 48)
Write-Secret -Name "EMAIL_OUTBOX_ENCRYPTION_PREVIOUS_KEYS" -Value "{}"
Write-Secret -Name "METRICS_TOKEN" -Value (New-RandomHex -Bytes 48)
Write-Secret -Name "MINIO_ROOT_USER" -Value $minioRootUser
Write-Secret -Name "MINIO_ROOT_PASSWORD" -Value $minioRootPassword
Write-Secret -Name "MINIO_ACCESS_KEY" -Value $minioAccessKey
Write-Secret -Name "MINIO_SECRET_KEY" -Value $minioSecretKey

if ([Environment]::OSVersion.Platform -eq "Win32NT") {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    $propagation = [Security.AccessControl.PropagationFlags]::None
    $acl.AddAccessRule(
        [Security.AccessControl.FileSystemAccessRule]::new(
            $identity,
            [Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            $propagation,
            [Security.AccessControl.AccessControlType]::Allow
        )
    )
    [IO.Directory]::SetAccessControl($output, $acl)
} else {
    & chmod 700 -- $output
    Get-ChildItem -LiteralPath $output -File | ForEach-Object {
        & chmod 600 -- $_.FullName
    }
}

Write-Host "Created 20 production secret files in a protected external directory."
