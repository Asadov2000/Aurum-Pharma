[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Path = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$useProvidedPaths = $PSBoundParameters.ContainsKey("Path")

function Invoke-GitLines {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & git @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        throw "Unable to list untracked files."
    }
    return @($output | ForEach-Object { "$_" } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
}

if ($useProvidedPaths) {
    $paths = @($Path)
}
else {
    Push-Location $repoRoot
    try {
        $paths = Invoke-GitLines -Arguments @("ls-files", "--others", "--exclude-standard")
    }
    finally {
        Pop-Location
    }
}

$paths = @($paths | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Sort-Object -Unique)
$nullDevice = if ($env:OS -eq "Windows_NT") { "NUL" } else { "/dev/null" }
$failedPaths = [System.Collections.Generic.List[string]]::new()

foreach ($pathValue in $paths) {
    $absolutePath = if ([IO.Path]::IsPathRooted($pathValue)) {
        [IO.Path]::GetFullPath($pathValue)
    }
    else {
        [IO.Path]::GetFullPath((Join-Path $repoRoot $pathValue))
    }

    if (-not (Test-Path -LiteralPath $absolutePath -PathType Leaf)) {
        throw "Untracked file disappeared during verification: $pathValue"
    }

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $checkOutput = & git diff --no-index --check -- $nullDevice $absolutePath 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    if ($exitCode -gt 1) {
        throw "Unable to inspect untracked file whitespace: $pathValue"
    }
    if (@($checkOutput).Count -gt 0) {
        $failedPaths.Add($pathValue)
    }
}

if ($failedPaths.Count -gt 0) {
    throw "Whitespace errors found in untracked files: $($failedPaths -join ', ')"
}

Write-Host "Untracked whitespace check passed: $($paths.Count) files."
