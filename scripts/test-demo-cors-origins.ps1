[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "demo-cors-origins.ps1")
$script:assertions = 0
$expectedLocal = @(
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:18080", "http://127.0.0.1:18080"
)

function Assert-Origins {
    param([string]$Json, [string[]]$Expected, [string]$Because)
    $actual = @(Get-DemoCorsOrigins -ServeStatusJson $Json)
    $script:assertions++
    if (($actual -join "|") -cne ($Expected -join "|")) {
        throw "Origin assertion failed: $Because"
    }
}

function New-ServeFixture {
    param(
        [string]$Authority = "demo.tail123.ts.net:443",
        [string]$Proxy = "http://127.0.0.1:18080",
        [object]$Https = $true,
        [string]$Port = "443",
        [string]$HandlerPath = "/",
        [hashtable]$AdditionalHandlers = @{}
    )
    $handlers = @{ $HandlerPath = @{ Proxy = $Proxy } }
    foreach ($name in $AdditionalHandlers.Keys) { $handlers[$name] = $AdditionalHandlers[$name] }
    return ConvertTo-Json -Depth 8 -Compress -InputObject @{
        TCP = @{ $Port = @{ HTTPS = $Https } }
        Web = @{ $Authority = @{ Handlers = $handlers } }
    }
}

foreach ($json in @("", "not json", "{}", "null", '{"Web":{}}')) {
    Assert-Origins -Json $json -Expected $expectedLocal -Because "missing/invalid status stays local"
}
Assert-Origins -Json (New-ServeFixture) -Expected ($expectedLocal + "https://demo.tail123.ts.net") `
    -Because "active HTTPS root proxy is admitted"
Assert-Origins -Json (New-ServeFixture -Proxy "http://localhost:18080/") `
    -Expected ($expectedLocal + "https://demo.tail123.ts.net") -Because "localhost gateway is admitted"
Assert-Origins -Json (New-ServeFixture -Authority "demo.tail123.ts.net:8443" -Port "8443") `
    -Expected ($expectedLocal + "https://demo.tail123.ts.net:8443") -Because "HTTPS port is explicit"

foreach ($authority in @(
    "example.com:443", "demo.tail123.ts.net.evil.test:443", "*.tail123.ts.net:443",
    "user@demo.tail123.ts.net:443", "demo.tail123.ts.net:443/path", "127.0.0.1:443"
)) {
    Assert-Origins -Json (New-ServeFixture -Authority $authority) -Expected $expectedLocal `
        -Because "arbitrary authority is rejected"
}
foreach ($proxy in @(
    "http://localhost:5173", "http://localhost:8000", "http://other-app:18080",
    "https://localhost:18080", "http://localhost:18080/other", "http://localhost:18080/?query=1",
    "http://user:pass@localhost:18080", "http://localhost:18080/#fragment"
)) {
    Assert-Origins -Json (New-ServeFixture -Proxy $proxy) -Expected $expectedLocal `
        -Because "non-gateway proxy is rejected"
}
Assert-Origins -Json (New-ServeFixture -Https $false) -Expected $expectedLocal `
    -Because "HTTP listener is rejected"
Assert-Origins -Json (New-ServeFixture -Https "true") -Expected $expectedLocal `
    -Because "malformed HTTPS flag is rejected"
Assert-Origins -Json (New-ServeFixture -Port "8443") -Expected $expectedLocal `
    -Because "unmatched listener is rejected"
Assert-Origins -Json (New-ServeFixture -HandlerPath "/other") -Expected $expectedLocal `
    -Because "unrelated path is rejected"
Assert-Origins -Json (New-ServeFixture -AdditionalHandlers @{
    "/api" = @{ Proxy = "http://localhost:8000" }
}) -Expected $expectedLocal -Because "auth route override to another service is rejected"
Assert-Origins -Json (New-ServeFixture -AdditionalHandlers @{
    "/api/v1/branches" = @{ Proxy = "http://localhost:8000" }
}) -Expected $expectedLocal -Because "data route override to another service is rejected"

$fallback = Get-DemoCorsOriginsJson -ReadServeStatus { throw "CLI unavailable" }
$fallbackOrigins = ConvertFrom-Json -InputObject $fallback
$script:assertions++
if (($fallbackOrigins -join "|") -cne ($expectedLocal -join "|")) {
    throw "An unavailable Tailscale CLI must preserve local-only origins"
}
$injected = Get-DemoCorsOriginsJson -ReadServeStatus { New-ServeFixture }
$injectedOrigins = ConvertFrom-Json -InputObject $injected
$script:assertions++
if ($injectedOrigins.Count -ne 5) { throw "The injectable reader was not used" }

$original = [Environment]::GetEnvironmentVariable("AURUM_DEMO_CORS_ORIGINS", "Process")
try {
    foreach ($previous in @($null, "previous-process-value")) {
        foreach ($failAction in @($false, $true)) {
            if ($null -eq $previous) {
                Remove-Item -LiteralPath Env:AURUM_DEMO_CORS_ORIGINS -ErrorAction SilentlyContinue
            }
            else {
                [Environment]::SetEnvironmentVariable("AURUM_DEMO_CORS_ORIGINS", $previous, "Process")
            }
            $threw = $false
            try {
                Invoke-WithDemoCorsOrigins -OriginsJson $injected -Action {
                    $script:assertions++
                    if ($env:AURUM_DEMO_CORS_ORIGINS -cne $injected) { throw "Origin export failed" }
                    if ($failAction) { throw "Simulated action failure" }
                }
            }
            catch {
                if ($_.Exception.Message -ne "Simulated action failure") { throw }
                $threw = $true
            }
            $script:assertions++
            if ($threw -ne $failAction) { throw "Action errors must propagate" }
            $restored = [Environment]::GetEnvironmentVariable("AURUM_DEMO_CORS_ORIGINS", "Process")
            $script:assertions++
            if ($restored -cne $previous) { throw "Previous process environment was not restored" }
        }
    }
}
finally {
    if ($null -eq $original) {
        Remove-Item -LiteralPath Env:AURUM_DEMO_CORS_ORIGINS -ErrorAction SilentlyContinue
    }
    else {
        [Environment]::SetEnvironmentVariable("AURUM_DEMO_CORS_ORIGINS", $original, "Process")
    }
}

Write-Host "Demo CORS origin checks passed: $script:assertions assertions."
