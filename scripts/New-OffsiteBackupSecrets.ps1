[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [Parameter(Mandatory = $true)]
    [Security.SecureString]$AccessKey,

    [Parameter(Mandatory = $true)]
    [Security.SecureString]$SecretKey
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
    throw "Off-site secrets must be stored outside the repository."
}

if (Test-Path -LiteralPath $output) {
    if (@(Get-ChildItem -LiteralPath $output -Force).Count -gt 0) {
        throw "The output directory must be empty. Existing secrets are never overwritten."
    }
} else {
    New-Item -ItemType Directory -Path $output | Out-Null
}

$utf8NoBom = [Text.UTF8Encoding]::new($false)
function Write-SecureSecret {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][Security.SecureString]$Value
    )

    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        if (
            [string]::IsNullOrWhiteSpace($plain) -or
            $plain.Contains("`n") -or
            $plain.Contains("`r")
        ) {
            throw "Secret $Name must be one non-empty line."
        }
        [IO.File]::WriteAllText((Join-Path $output $Name), $plain, $utf8NoBom)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        $plain = $null
    }
}

Write-SecureSecret -Name "AURUM_OFFSITE_ACCESS_KEY" -Value $AccessKey
Write-SecureSecret -Name "AURUM_OFFSITE_SECRET_KEY" -Value $SecretKey

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
    Get-ChildItem -LiteralPath $output -File | ForEach-Object {
        & chmod 600 -- $_.FullName
    }
}

Write-Host "Created 2 off-site credential files in a protected external directory."
