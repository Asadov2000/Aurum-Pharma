[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib/windows-tpm-readiness.ps1")
$script:assertions = 0

function Assert-Equal {
    param(
        [AllowNull()][object]$Actual,
        [AllowNull()][object]$Expected,
        [Parameter(Mandatory = $true)][string]$Because
    )

    $script:assertions++
    if ("$Actual" -cne "$Expected") {
        throw "Assertion failed ($Because). Expected '$Expected', got '$Actual'."
    }
}

$positive = '{"schemaVersion":1,"status":"read-only-available","safeOnBothReads":true,"continuityObserved":true,"counterProgressObserved":true,"powerGuardContinuityObserved":true,"runtimeAdmission":false,"hardwareAcceptance":false}'
Assert-Equal (Test-TpmProbeResponse -ExitCode 0 -Output $positive) $true "the exact positive capability response is accepted"
Assert-Equal (Test-TpmProbeResponse -ExitCode 0 -Output (" `r`n" + $positive + "`r`n ")) $true "surrounding whitespace is allowed"
$reordered = '{"hardwareAcceptance":false,"runtimeAdmission":false,"powerGuardContinuityObserved":true,"counterProgressObserved":true,"continuityObserved":true,"safeOnBothReads":true,"status":"read-only-available","schemaVersion":1}'
Assert-Equal (Test-TpmProbeResponse -ExitCode 0 -Output $reordered) $true "property order does not affect the strict schema"

foreach ($exitCode in @(-1, 1, 2, 255)) {
    Assert-Equal (Test-TpmProbeResponse -ExitCode $exitCode -Output $positive) $false "nonzero exit $exitCode cannot prove an observation"
}

$invalidResponses = @(
    @{ Name = "empty output"; Value = "" },
    @{ Name = "whitespace only"; Value = " `r`n " },
    @{ Name = "truncated JSON"; Value = $positive.Substring(0, $positive.Length - 1) },
    @{ Name = "array wrapper"; Value = "[$positive]" },
    @{ Name = "JSON null"; Value = "null" },
    @{ Name = "JSON scalar"; Value = '"read-only-available"' },
    @{ Name = "prefix diagnostics"; Value = "diagnostic`r`n$positive" },
    @{ Name = "suffix diagnostics"; Value = "$positive`r`ndiagnostic" },
    @{ Name = "multiple responses"; Value = "$positive`r`n$positive" },
    @{ Name = "oversized output"; Value = (" " * 2048) + $positive },
    @{ Name = "unknown property"; Value = $positive.Replace('"schemaVersion":1', '"unexpected":true,"schemaVersion":1') },
    @{ Name = "duplicate property"; Value = $positive.Replace('"schemaVersion":1', '"schemaVersion":1,"schemaVersion":1') },
    @{ Name = "contradictory duplicate"; Value = $positive.Replace('"safeOnBothReads":true', '"safeOnBothReads":false,"safeOnBothReads":true') },
    @{ Name = "property case mismatch"; Value = $positive.Replace('"status"', '"Status"') },
    @{ Name = "status case mismatch"; Value = $positive.Replace('read-only-available', 'Read-only-available') },
    @{ Name = "unavailable status"; Value = $positive.Replace('read-only-available', 'read-only-unavailable') },
    @{ Name = "escaped property name"; Value = $positive.Replace('"status"', '"st\u0061tus"') },
    @{ Name = "schema version string"; Value = $positive.Replace('"schemaVersion":1', '"schemaVersion":"1"') },
    @{ Name = "schema version float"; Value = $positive.Replace('"schemaVersion":1', '"schemaVersion":1.0') },
    @{ Name = "schema version exponent"; Value = $positive.Replace('"schemaVersion":1', '"schemaVersion":1e0') },
    @{ Name = "schema version two"; Value = $positive.Replace('"schemaVersion":1', '"schemaVersion":2') },
    @{ Name = "trailing comma"; Value = $positive.Substring(0, $positive.Length - 1) + ',}' },
    @{ Name = "JSON comment"; Value = $positive.Replace('"schemaVersion":1', '/* comment */"schemaVersion":1') }
)
foreach ($field in @("safeOnBothReads", "continuityObserved", "counterProgressObserved", "powerGuardContinuityObserved")) {
    foreach ($value in @("false", '"true"', "1", "null", "[]", "{}")) {
        $invalidResponses += @{ Name = "$field must be true, not $value"; Value = $positive.Replace(('"' + $field + '":true'), ('"' + $field + '":' + $value)) }
    }
}
foreach ($field in @("runtimeAdmission", "hardwareAcceptance")) {
    foreach ($value in @("true", '"false"', "0", "null")) {
        $invalidResponses += @{ Name = "$field must stay false, not $value"; Value = $positive.Replace(('"' + $field + '":false'), ('"' + $field + '":' + $value)) }
    }
}
foreach ($field in @("schemaVersion", "status", "safeOnBothReads", "continuityObserved", "counterProgressObserved", "powerGuardContinuityObserved", "runtimeAdmission", "hardwareAcceptance")) {
    $withoutField = [regex]::Replace($positive, ('"' + $field + '":(?:"[^"]*"|true|false|1),?'), "")
    $withoutField = $withoutField.Replace(',}', '}')
    $invalidResponses += @{ Name = "missing $field"; Value = $withoutField }
}
foreach ($response in $invalidResponses) {
    Assert-Equal (Test-TpmProbeResponse -ExitCode 0 -Output $response.Value) $false $response.Name
}

$repoRoot = Split-Path $PSScriptRoot -Parent
$fixtureRoot = Join-Path $repoRoot (".codex/tpm readiness tests " + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $fixtureRoot | Out-Null
$fixturePath = Join-Path $fixtureRoot "fake probe.ps1"
try {
    $missing = Get-WindowsTpmReadiness -RepoRoot $fixtureRoot
    $expectedMissing = if ($env:OS -eq "Windows_NT") { "not-built" } else { "not-supported" }
    Assert-Equal $missing.Status $expectedMissing "a fixture without the fixed artifact never runs native TPM"

    $powershellExecutable = (Get-Process -Id $PID).Path
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $fixturePath + '"'
    Set-Content -LiteralPath $fixturePath -Encoding UTF8 -Value ("[Console]::Out.WriteLine('" + $positive + "'); exit 0")
    $success = Invoke-TpmReadinessProcess -ExecutablePath $powershellExecutable -Arguments $arguments -TimeoutMilliseconds 10000
    Assert-Equal $success.Status "observed" "runner accepts a successful strict response through a path containing spaces"

    Set-Content -LiteralPath $fixturePath -Encoding UTF8 -Value ("[Console]::Out.WriteLine('" + $positive + "'); exit 2")
    $failed = Invoke-TpmReadinessProcess -ExecutablePath $powershellExecutable -Arguments $arguments -TimeoutMilliseconds 10000
    Assert-Equal $failed.Status "unavailable" "a positive stdout with a failing process stays unavailable"

    Set-Content -LiteralPath $fixturePath -Encoding UTF8 -Value ("[Console]::Out.WriteLine('" + $positive + "'); [Console]::Error.WriteLine('diagnostic'); exit 0")
    $unexpectedStderr = Invoke-TpmReadinessProcess -ExecutablePath $powershellExecutable -Arguments $arguments -TimeoutMilliseconds 10000
    Assert-Equal $unexpectedStderr.Status "unavailable" "a nominal success accompanied by stderr cannot claim an observation"

    Set-Content -LiteralPath $fixturePath -Encoding UTF8 -Value "[Console]::Error.WriteLine(('x' * 262144)); exit 1"
    $stderr = Invoke-TpmReadinessProcess -ExecutablePath $powershellExecutable -Arguments $arguments -TimeoutMilliseconds 10000
    Assert-Equal $stderr.Status "unavailable" "large stderr is drained without deadlock"
    Assert-Equal (($stderr | ConvertTo-Json -Compress).Contains(('x' * 20))) $false "raw native diagnostics are not exposed in the result"

    Set-Content -LiteralPath $fixturePath -Encoding UTF8 -Value "[System.Threading.Thread]::Sleep(10000)"
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    $timedOut = Invoke-TpmReadinessProcess -ExecutablePath $powershellExecutable -Arguments $arguments -TimeoutMilliseconds 200
    $timer.Stop()
    Assert-Equal $timedOut.Status "timeout" "runner reports the bounded deadline"
    Assert-Equal ($timer.ElapsedMilliseconds -lt 5000) $true "timeout does not wait for fixture completion"

    $notExecutable = Join-Path $fixtureRoot "missing executable.exe"
    $missingExecutable = Invoke-TpmReadinessProcess -ExecutablePath $notExecutable -Arguments $arguments -TimeoutMilliseconds 200
    Assert-Equal $missingExecutable.Status "unavailable" "process startup failure is reported without native diagnostics"
}
finally {
    if (Test-Path -LiteralPath $fixturePath -PathType Leaf) {
        Remove-Item -LiteralPath $fixturePath
    }
}

Write-Output "Windows TPM readiness: $script:assertions assertions passed."
