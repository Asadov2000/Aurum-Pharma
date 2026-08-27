[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$wrapper = Join-Path $PSScriptRoot "Manage-EdgeCashIdentity.ps1"
$temporaryDirectory = Join-Path $PSScriptRoot (
    ".tmp-edge-wrapper-{0}" -f [guid]::NewGuid().ToString("N")
)
$dockerMock = Join-Path $temporaryDirectory "docker.cmd"
$outputFile = Join-Path $temporaryDirectory "docker-arguments.txt"
$originalPath = $env:PATH
$originalOutput = $env:AURUM_DOCKER_MOCK_OUTPUT
$originalExitCode = $env:AURUM_DOCKER_MOCK_EXIT
$edgeNodeId = "A21E3B44-5F61-4A74-9268-60C8181569FA"

try {
    New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null
    @(
        '@echo off'
        'echo %* > "%AURUM_DOCKER_MOCK_OUTPUT%"'
        'exit /b %AURUM_DOCKER_MOCK_EXIT%'
    ) | Set-Content -LiteralPath $dockerMock -Encoding Ascii

    $env:PATH = "$temporaryDirectory$([System.IO.Path]::PathSeparator)$originalPath"
    $env:AURUM_DOCKER_MOCK_OUTPUT = $outputFile
    $env:AURUM_DOCKER_MOCK_EXIT = "0"

    & $wrapper -EdgeNodeId $edgeNodeId -Action "REVOKE"
    $arguments = (Get-Content -LiteralPath $outputFile -Raw).Trim()
    $expected = (
        "compose --profile maintenance run --rm " +
        "-e EDGE_NODE_ID=$($edgeNodeId.ToLowerInvariant()) " +
        "-e EDGE_IDENTITY_ACTION=revoke edge-cash-identity"
    )
    if ($arguments -cne $expected) {
        throw "Unexpected Docker arguments. Expected '$expected', got '$arguments'."
    }

    $env:AURUM_DOCKER_MOCK_EXIT = "17"
    $failedClosed = $false
    try {
        & $wrapper -EdgeNodeId $edgeNodeId
    }
    catch {
        $failedClosed = $_.Exception.Message -like "*exit code 17*"
    }
    if (-not $failedClosed) {
        throw "The wrapper did not propagate the Docker failure."
    }

    Write-Host "Edge identity Windows wrapper checks passed."
}
finally {
    $env:PATH = $originalPath
    $env:AURUM_DOCKER_MOCK_OUTPUT = $originalOutput
    $env:AURUM_DOCKER_MOCK_EXIT = $originalExitCode
    Remove-Item -LiteralPath $dockerMock, $outputFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $temporaryDirectory -Force -ErrorAction SilentlyContinue
}
