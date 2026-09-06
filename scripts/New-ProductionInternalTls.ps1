[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [ValidateRange(31, 825)]
    [int]$CertificateValidityDays = 397,

    [ValidateRange(397, 3650)]
    [int]$CaValidityDays = 1825
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "PowerShell 7 or newer is required to generate production TLS material."
}

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
    throw "Production TLS material must be stored outside the repository."
}

if (Test-Path -LiteralPath $output) {
    if (@(Get-ChildItem -LiteralPath $output -Force).Count -gt 0) {
        throw "The output directory must be empty. Existing TLS material is never overwritten."
    }
} else {
    New-Item -ItemType Directory -Path $output | Out-Null
}

$utf8NoBom = [Text.UTF8Encoding]::new($false)

function ConvertTo-Pem {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][byte[]]$Bytes
    )

    $base64 = [Convert]::ToBase64String(
        $Bytes,
        [Base64FormattingOptions]::InsertLineBreaks
    )
    return "-----BEGIN $Label-----`n$base64`n-----END $Label-----`n"
}

function Write-PemFile {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Content
    )

    [IO.File]::WriteAllText((Join-Path $output $Name), $Content, $utf8NoBom)
}

function New-SerialNumber {
    $serial = [byte[]]::new(20)
    [Security.Cryptography.RandomNumberGenerator]::Fill($serial)
    $serial[0] = $serial[0] -band 0x7f
    if (($serial | Where-Object { $_ -ne 0 }).Count -eq 0) {
        $serial[$serial.Length - 1] = 1
    }
    return $serial
}

function New-ServiceCertificate {
    param(
        [Parameter(Mandatory = $true)][string]$ServiceName,
        [Parameter(Mandatory = $true)]
        [Security.Cryptography.X509Certificates.X509Certificate2]$Issuer,
        [Parameter(Mandatory = $true)][datetimeoffset]$NotBefore,
        [Parameter(Mandatory = $true)][datetimeoffset]$NotAfter
    )

    $key = [Security.Cryptography.RSA]::Create(3072)
    try {
        $request = [Security.Cryptography.X509Certificates.CertificateRequest]::new(
            "CN=$ServiceName",
            $key,
            [Security.Cryptography.HashAlgorithmName]::SHA256,
            [Security.Cryptography.RSASignaturePadding]::Pkcs1
        )
        $request.CertificateExtensions.Add(
            [Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new(
                $false,
                $false,
                0,
                $true
            )
        )
        $request.CertificateExtensions.Add(
            [Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new(
                [Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature -bor
                [Security.Cryptography.X509Certificates.X509KeyUsageFlags]::KeyEncipherment,
                $true
            )
        )
        $enhancedKeyUsage = [Security.Cryptography.OidCollection]::new()
        [void]$enhancedKeyUsage.Add([Security.Cryptography.Oid]::new("1.3.6.1.5.5.7.3.1"))
        $request.CertificateExtensions.Add(
            [Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]::new(
                $enhancedKeyUsage,
                $true
            )
        )
        $san = [Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new()
        $san.AddDnsName($ServiceName)
        $san.AddDnsName("localhost")
        $san.AddIpAddress([Net.IPAddress]::Loopback)
        $request.CertificateExtensions.Add($san.Build($true))

        $certificate = $request.Create(
            $Issuer,
            $NotBefore,
            $NotAfter,
            (New-SerialNumber)
        )
        try {
            Write-PemFile -Name "$ServiceName.crt" -Content (
                ConvertTo-Pem -Label "CERTIFICATE" -Bytes $certificate.RawData
            )
            Write-PemFile -Name "$ServiceName.key" -Content (
                ConvertTo-Pem -Label "PRIVATE KEY" -Bytes $key.ExportPkcs8PrivateKey()
            )
        } finally {
            $certificate.Dispose()
        }
    } finally {
        $key.Dispose()
    }
}

$notBefore = [DateTimeOffset]::UtcNow.AddMinutes(-5)
$caKey = [Security.Cryptography.RSA]::Create(4096)
try {
    $caRequest = [Security.Cryptography.X509Certificates.CertificateRequest]::new(
        "CN=Aurum Pharma Internal Root CA",
        $caKey,
        [Security.Cryptography.HashAlgorithmName]::SHA256,
        [Security.Cryptography.RSASignaturePadding]::Pkcs1
    )
    $caRequest.CertificateExtensions.Add(
        [Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new(
            $true,
            $true,
            0,
            $true
        )
    )
    $caRequest.CertificateExtensions.Add(
        [Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new(
            [Security.Cryptography.X509Certificates.X509KeyUsageFlags]::KeyCertSign -bor
            [Security.Cryptography.X509Certificates.X509KeyUsageFlags]::CrlSign,
            $true
        )
    )
    $caRequest.CertificateExtensions.Add(
        [Security.Cryptography.X509Certificates.X509SubjectKeyIdentifierExtension]::new(
            $caRequest.PublicKey,
            $false
        )
    )
    $caCertificate = $caRequest.CreateSelfSigned(
        $notBefore,
        $notBefore.AddDays($CaValidityDays)
    )
    try {
        Write-PemFile -Name "ca.crt" -Content (
            ConvertTo-Pem -Label "CERTIFICATE" -Bytes $caCertificate.RawData
        )
        Write-PemFile -Name "ca.key" -Content (
            ConvertTo-Pem -Label "PRIVATE KEY" -Bytes $caKey.ExportPkcs8PrivateKey()
        )

        $certificateExpiry = $notBefore.AddDays($CertificateValidityDays)
        foreach ($serviceName in @("postgres", "redis", "minio")) {
            New-ServiceCertificate `
                -ServiceName $serviceName `
                -Issuer $caCertificate `
                -NotBefore $notBefore `
                -NotAfter $certificateExpiry
        }
    } finally {
        $caCertificate.Dispose()
    }
} finally {
    $caKey.Dispose()
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
    Get-ChildItem -LiteralPath $output -File -Filter "*.key" | ForEach-Object {
        & chmod 600 -- $_.FullName
    }
    Get-ChildItem -LiteralPath $output -File -Filter "*.crt" | ForEach-Object {
        & chmod 644 -- $_.FullName
    }
}

Write-Host "Created internal CA and service certificates outside the repository."
Write-Host "Keep ca.key offline from runtime containers and rotate service certificates before expiry."
