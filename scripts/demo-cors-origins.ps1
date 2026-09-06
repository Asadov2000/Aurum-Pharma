# Dot-sourceable demo-only helpers. No application secrets or HTTP headers are read.

function Get-DemoServeProperty {
    param([AllowNull()][object]$Value, [string]$Name)
    if ($null -eq $Value) { return $null }
    $property = $Value.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Test-DemoGatewayProxy {
    param([AllowNull()][object]$Value)
    if ($Value -isnot [string]) { return $false }
    $proxy = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$proxy)) { return $false }
    return (
        $proxy.Scheme -eq "http" -and
        $proxy.Host -in @("localhost", "127.0.0.1") -and
        $proxy.Port -eq 18080 -and
        $proxy.AbsolutePath -eq "/" -and
        $proxy.UserInfo -eq "" -and $proxy.Query -eq "" -and $proxy.Fragment -eq ""
    )
}

function Get-DemoCorsOrigins {
    param([AllowNull()][AllowEmptyString()][string]$ServeStatusJson)

    $origins = New-Object 'System.Collections.Generic.List[string]'
    foreach ($origin in @(
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:18080", "http://127.0.0.1:18080"
    )) { $origins.Add($origin) }

    $status = $null
    if (-not [string]::IsNullOrWhiteSpace($ServeStatusJson)) {
        try { $status = ConvertFrom-Json -InputObject $ServeStatusJson -ErrorAction Stop }
        catch { $status = $null }
    }
    $web = Get-DemoServeProperty -Value $status -Name "Web"
    $tcp = Get-DemoServeProperty -Value $status -Name "TCP"
    if ($null -ne $web -and $null -ne $tcp) {
        foreach ($entry in $web.PSObject.Properties) {
            # Serve's host:port key must be a concrete Tailscale DNS authority.
            if ($entry.Name -notmatch '^[a-z0-9][a-z0-9.-]*:[0-9]{1,5}$') { continue }
            $url = $null
            if (-not [Uri]::TryCreate("https://$($entry.Name)", [UriKind]::Absolute, [ref]$url)) {
                continue
            }
            if ($url.DnsSafeHost -notmatch '^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+ts\.net$') {
                continue
            }
            $port = Get-DemoServeProperty -Value $tcp -Name ([string]$url.Port)
            $https = Get-DemoServeProperty -Value $port -Name "HTTPS"
            if ($https -isnot [bool] -or -not $https) { continue }
            $handlers = Get-DemoServeProperty -Value $entry.Value -Name "Handlers"
            $root = Get-DemoServeProperty -Value $handlers -Name "/"
            $rootProxy = Get-DemoServeProperty -Value $root -Name "Proxy"
            if (-not (Test-DemoGatewayProxy -Value $rootProxy)) { continue }

            # Every API route must reach this demo, not another application's data.
            $apiIsOurs = $true
            foreach ($handler in $handlers.PSObject.Properties) {
                $path = $handler.Name
                if ($path -eq "/") { continue }
                if ("/api/".StartsWith($path, [StringComparison]::Ordinal) -or
                    $path.StartsWith("/api/", [StringComparison]::Ordinal)) {
                    $proxy = Get-DemoServeProperty -Value $handler.Value -Name "Proxy"
                    if (-not (Test-DemoGatewayProxy -Value $proxy)) { $apiIsOurs = $false }
                }
            }
            if (-not $apiIsOurs) { continue }
            $origin = $url.GetLeftPart([UriPartial]::Authority).ToLowerInvariant()
            if (-not $origins.Contains($origin)) { $origins.Add($origin) }
        }
    }
    return $origins.ToArray()
}

function Read-DemoTailscaleServeStatus {
    $command = Get-Command tailscale -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $commandPath = if ($null -ne $command) { $command.Source } else { $null }
    if (-not $commandPath) {
        $programFilesPath = [Environment]::GetEnvironmentVariable("ProgramFiles")
        if ($programFilesPath) {
            $candidate = Join-Path $programFilesPath "Tailscale\tailscale.exe"
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { $commandPath = $candidate }
        }
    }
    if (-not $commandPath) { return "" }
    $output = & $commandPath serve status --json 2>$null
    if ($LASTEXITCODE -ne 0) { return "" }
    return ($output -join [Environment]::NewLine)
}

function Get-DemoCorsOriginsJson {
    param([scriptblock]$ReadServeStatus = { Read-DemoTailscaleServeStatus })
    $statusJson = ""
    try { $statusJson = (& $ReadServeStatus | Out-String) }
    catch { $statusJson = "" }
    $origins = @(Get-DemoCorsOrigins -ServeStatusJson $statusJson)
    return ConvertTo-Json -InputObject $origins -Compress
}

function Invoke-WithDemoCorsOrigins {
    param(
        [Parameter(Mandatory = $true)][string]$OriginsJson,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )
    $previousOrigins = [Environment]::GetEnvironmentVariable("AURUM_DEMO_CORS_ORIGINS", "Process")
    try {
        [Environment]::SetEnvironmentVariable("AURUM_DEMO_CORS_ORIGINS", $OriginsJson, "Process")
        & $Action
    }
    finally {
        if ($null -eq $previousOrigins) {
            Remove-Item -LiteralPath Env:AURUM_DEMO_CORS_ORIGINS -ErrorAction SilentlyContinue
        }
        else {
            [Environment]::SetEnvironmentVariable("AURUM_DEMO_CORS_ORIGINS", $previousOrigins, "Process")
        }
    }
}
