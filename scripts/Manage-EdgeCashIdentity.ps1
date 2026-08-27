[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')]
    [string]$EdgeNodeId,

    [ValidateSet('enroll', 'revoke')]
    [string]$Action = 'enroll'
)

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot

Push-Location $workspace
try {
    $normalizedAction = $Action.ToLowerInvariant()
    & docker compose --profile maintenance run --rm `
        -e "EDGE_NODE_ID=$($EdgeNodeId.ToLowerInvariant())" `
        -e "EDGE_IDENTITY_ACTION=$normalizedAction" `
        edge-cash-identity
    if ($LASTEXITCODE -ne 0) {
        throw "Edge identity command failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
