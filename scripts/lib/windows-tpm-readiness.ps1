# Read-only developer preflight. No keys, enrollment, admission or trusted UTC.
# Keep compatible with Windows PowerShell 5.1 and PowerShell 7.

function Test-TpmProbeResponse {
    param(
        [int]$ExitCode,
        [AllowNull()][AllowEmptyString()][string]$Output
    )

    if ($ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($Output) -or $Output.Length -gt 2048) {
        return $false
    }

    # The fixed probe emits a flat object with unescaped ASCII keys, booleans,
    # an integer version and one status string. Reject comments, duplicate keys,
    # nested values and trailing diagnostics before ConvertFrom-Json, whose
    # permissiveness differs between supported PowerShell versions.
    $property = '"[A-Za-z]+"\s*:\s*(?:true|false|1|"read-only-available")'
    if ($Output -cnotmatch ('\A\s*\{\s*' + $property + '(?:\s*,\s*' + $property + ')*\s*\}\s*\z')) {
        return $false
    }

    $expected = @(
        'schemaVersion', 'status', 'safeOnBothReads', 'continuityObserved',
        'counterProgressObserved', 'powerGuardContinuityObserved',
        'runtimeAdmission', 'hardwareAcceptance'
    )
    $keys = [regex]::Matches($Output, '"([A-Za-z]+)"\s*:')
    if ($keys.Count -ne $expected.Count) { return $false }
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    foreach ($key in $keys) {
        $name = $key.Groups[1].Value
        if ($expected -cnotcontains $name -or -not $seen.Add($name)) { return $false }
    }

    try {
        $result = ConvertFrom-Json -InputObject $Output -ErrorAction Stop
        if ($result.schemaVersion -isnot [int] -and $result.schemaVersion -isnot [long]) { return $false }
        if ($result.schemaVersion -ne 1 -or $result.status -cne 'read-only-available') { return $false }
        foreach ($name in @('safeOnBothReads', 'continuityObserved', 'counterProgressObserved', 'powerGuardContinuityObserved')) {
            if ($result.$name -isnot [bool] -or -not $result.$name) { return $false }
        }
        foreach ($name in @('runtimeAdmission', 'hardwareAcceptance')) {
            if ($result.$name -isnot [bool] -or $result.$name) { return $false }
        }
        return $true
    }
    catch { return $false }
}

function Invoke-TpmReadinessProcess {
    param(
        [Parameter(Mandatory = $true)][string]$ExecutablePath,
        [Parameter(Mandatory = $true)][string]$Arguments,
        [ValidateRange(1, 30000)][int]$TimeoutMilliseconds = 15000
    )

    $probeProcess = New-Object System.Diagnostics.Process
    try {
        $probeProcess.StartInfo.FileName = $ExecutablePath
        $probeProcess.StartInfo.Arguments = $Arguments
        $probeProcess.StartInfo.UseShellExecute = $false
        $probeProcess.StartInfo.CreateNoWindow = $true
        $probeProcess.StartInfo.RedirectStandardOutput = $true
        $probeProcess.StartInfo.RedirectStandardError = $true
        $probeProcess.StartInfo.EnvironmentVariables['DOTNET_NOLOGO'] = '1'
        $probeProcess.StartInfo.EnvironmentVariables['DOTNET_CLI_TELEMETRY_OPTOUT'] = '1'
        if (-not $probeProcess.Start()) { return [pscustomobject]@{ Status = 'unavailable' } }

        # Drain both pipes concurrently so stderr cannot deadlock a waiting parent.
        $stdout = $probeProcess.StandardOutput.ReadToEndAsync()
        $stderr = $probeProcess.StandardError.ReadToEndAsync()
        if (-not $probeProcess.WaitForExit($TimeoutMilliseconds)) {
            return [pscustomobject]@{ Status = 'timeout' }
        }
        $tasks = [System.Threading.Tasks.Task[]]@($stdout, $stderr)
        if (-not [System.Threading.Tasks.Task]::WaitAll($tasks, 1000) -or
            -not [string]::IsNullOrWhiteSpace($stderr.Result) -or
            -not (Test-TpmProbeResponse -ExitCode $probeProcess.ExitCode -Output $stdout.Result)) {
            return [pscustomobject]@{ Status = 'unavailable' }
        }
        return [pscustomobject]@{ Status = 'observed' }
    }
    catch { return [pscustomobject]@{ Status = 'unavailable' } }
    finally {
        try {
            if (-not $probeProcess.HasExited) {
                $probeProcess.Kill()
                $null = $probeProcess.WaitForExit(1000)
            }
        }
        catch { <# Cleanup must not disclose native diagnostics. #> }
        $probeProcess.Dispose()
    }
}

function Get-WindowsTpmReadiness {
    param([Parameter(Mandatory = $true)][string]$RepoRoot)

    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        return [pscustomobject]@{ Status = 'not-supported' }
    }
    $probePath = Join-Path $RepoRoot 'desktop/AurumPharma.Desktop.TpmProbe/bin/Release/net10.0/win-x64/AurumPharma.Desktop.TpmProbe.dll'
    if (-not (Test-Path -LiteralPath $probePath -PathType Leaf)) {
        return [pscustomobject]@{ Status = 'not-built' }
    }
    $dotnet = Get-Command dotnet -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $dotnet) { return [pscustomobject]@{ Status = 'runtime-missing' } }
    # Use only the fixed existing build, with no run/build/restore or shell.
    if ($probePath -match '["\r\n]') { return [pscustomobject]@{ Status = 'unavailable' } }
    return Invoke-TpmReadinessProcess -ExecutablePath $dotnet.Source -Arguments ('exec "' + $probePath + '"')
}
