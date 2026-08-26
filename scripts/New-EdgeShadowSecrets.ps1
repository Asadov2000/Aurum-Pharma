[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [Parameter(Mandatory = $true)]
    [Security.SecureString]$EdgeCredential
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

if ($output.Equals($workspace, $comparison) -or $output.StartsWith($workspacePrefix, $comparison)) {
    throw "Edge secrets must be stored outside the repository."
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

$passwords = @{
    POSTGRES_PASSWORD = New-RandomHex -Bytes 32
    AURUM_APP_PASSWORD = New-RandomHex -Bytes 32
    AURUM_SUPPORT_PASSWORD = New-RandomHex -Bytes 32
    AURUM_MIGRATOR_PASSWORD = New-RandomHex -Bytes 32
    AURUM_MAILER_PASSWORD = New-RandomHex -Bytes 32
    AURUM_BILLING_WORKER_PASSWORD = New-RandomHex -Bytes 32
    AURUM_BACKUP_PASSWORD = New-RandomHex -Bytes 32
    AURUM_PITR_PASSWORD = New-RandomHex -Bytes 32
}
foreach ($entry in $passwords.GetEnumerator()) {
    Write-Secret -Name $entry.Key -Value $entry.Value
}

Write-Secret -Name "DATABASE_URL_APP" -Value (
    "postgresql+asyncpg://aurum_app:{0}@edge-postgres:5432/aurum_edge" -f $passwords.AURUM_APP_PASSWORD
)
Write-Secret -Name "DATABASE_URL_SUPPORT" -Value (
    "postgresql+asyncpg://aurum_support:{0}@edge-postgres:5432/aurum_edge" -f $passwords.AURUM_SUPPORT_PASSWORD
)
Write-Secret -Name "DATABASE_URL_MIGRATION" -Value (
    "postgresql+asyncpg://aurum_migrator:{0}@edge-postgres:5432/aurum_edge" -f $passwords.AURUM_MIGRATOR_PASSWORD
)
Write-Secret -Name "JWT_SECRET" -Value (New-RandomHex -Bytes 48)

$credentialPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($EdgeCredential)
try {
    $credentialPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($credentialPointer)
    Write-Secret -Name "EDGE_SYNC_CREDENTIAL" -Value $credentialPlain
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($credentialPointer)
    $credentialPlain = $null
}

if ([Environment]::OSVersion.Platform -eq "Win32NT") {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    $acl.AddAccessRule(
        [Security.AccessControl.FileSystemAccessRule]::new(
            $identity,
            [Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow
        )
    )
    Set-Acl -LiteralPath $output -AclObject $acl
} else {
    & chmod 700 -- $output
    Get-ChildItem -LiteralPath $output -File | ForEach-Object { & chmod 600 -- $_.FullName }
}

Write-Host "Created 13 Edge shadow secret files in a protected external directory."
