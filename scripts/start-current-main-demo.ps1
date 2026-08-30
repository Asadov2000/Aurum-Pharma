[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Rebuild,
    [switch]$SkipSeed,
    [switch]$NoBrowser,
    [switch]$PauseOnError
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtimeRoot = Join-Path $workspace ".codex\runtime-main"
$runtimeStateRoot = Join-Path $workspace ".codex"
$imageRevisionFile = Join-Path $runtimeStateRoot "runtime-main-image-revision"
$failed = $false

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$AllowFailure
    )

    $previousPreference = $ErrorActionPreference
    $output = @()
    $exitCode = 1
    $ErrorActionPreference = "Continue"
    try {
        $output = @(& git -C $WorkingDirectory @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "git $($Arguments -join ' ') failed with exit code $exitCode"
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = $output
    }
}

function Resolve-Revision {
    param(
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$Revision
    )

    $result = Invoke-Git -WorkingDirectory $WorkingDirectory -Arguments @(
        "rev-parse", "--verify", "$Revision^{commit}"
    )
    $value = "$($result.Output[0])".Trim()
    if ($value -notmatch "^[0-9a-f]{40}$") {
        throw "Unable to resolve Git revision: $Revision"
    }
    return $value
}

function Assert-CleanRuntime {
    $topLevel = Invoke-Git -WorkingDirectory $runtimeRoot -Arguments @(
        "rev-parse", "--show-toplevel"
    )
    $resolvedTopLevel = (Resolve-Path "$($topLevel.Output[0])").Path
    $resolvedRuntime = (Resolve-Path $runtimeRoot).Path
    if ($resolvedTopLevel -cne $resolvedRuntime) {
        throw "Runtime path is not the expected Git worktree: $runtimeRoot"
    }

    $status = Invoke-Git -WorkingDirectory $runtimeRoot -Arguments @(
        "status", "--porcelain=v1", "--untracked-files=all"
    )
    if ($status.Output.Count -gt 0) {
        throw "Runtime worktree contains local changes. Refusing to overwrite it."
    }
}

function Invoke-LocalLauncher {
    param(
        [Parameter(Mandatory = $true)][string]$Launcher,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $powershellExecutable = (Get-Process -Id $PID).Path
    $externalArguments = @("-NoProfile")
    if ($env:OS -eq "Windows_NT") {
        $externalArguments += @("-ExecutionPolicy", "Bypass")
    }
    $externalArguments += @("-File", $Launcher)
    $externalArguments += $Arguments

    & $powershellExecutable @externalArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Aurum Pharma launcher failed with exit code $LASTEXITCODE"
    }
}

try {
    Write-Host "Aurum Pharma current-main launcher"
    Write-Host "Workspace: $workspace"
    Write-Host "Runtime:   $runtimeRoot"

    if ($DryRun) {
        Write-Host "Current main would be fetched into the isolated runtime worktree."
        $dryRunLauncher = Join-Path $PSScriptRoot "start-local-demo-admin.ps1"
        Invoke-LocalLauncher -Launcher $dryRunLauncher -Arguments @("-DryRun", "-NoBrowser")
        exit 0
    }

    $fetch = Invoke-Git -WorkingDirectory $workspace -Arguments @(
        "fetch", "--prune", "origin", "main"
    ) -AllowFailure
    if ($fetch.ExitCode -ne 0) {
        Write-Warning "GitHub is unavailable. Using the newest locally cached origin/main."
    }

    $targetRevision = Resolve-Revision -WorkingDirectory $workspace -Revision "origin/main"

    if (-not (Test-Path -LiteralPath $runtimeRoot)) {
        [IO.Directory]::CreateDirectory($runtimeStateRoot) | Out-Null
        Invoke-Git -WorkingDirectory $workspace -Arguments @(
            "worktree", "add", "--detach", $runtimeRoot, $targetRevision
        ) | Out-Null
    }

    Assert-CleanRuntime
    $runtimeRevision = Resolve-Revision -WorkingDirectory $runtimeRoot -Revision "HEAD"
    if ($runtimeRevision -ne $targetRevision) {
        Write-Host "Updating runtime to $($targetRevision.Substring(0, 7))..."
        Invoke-Git -WorkingDirectory $runtimeRoot -Arguments @(
            "switch", "--detach", $targetRevision
        ) | Out-Null
        Assert-CleanRuntime
    }
    else {
        Write-Host "Runtime is current: $($targetRevision.Substring(0, 7))"
    }

    $builtRevision = if (Test-Path -LiteralPath $imageRevisionFile -PathType Leaf) {
        (Get-Content -LiteralPath $imageRevisionFile -Raw).Trim()
    }
    else {
        ""
    }
    $needsRebuild = $Rebuild -or $builtRevision -ne $targetRevision

    $launcher = Join-Path $runtimeRoot "scripts\start-local-demo-admin.ps1"
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
        throw "Runtime launcher not found: $launcher"
    }

    $launcherArguments = @()
    if ($needsRebuild) {
        Write-Host "Application revision changed; Docker images will be rebuilt."
        $launcherArguments += "-Rebuild"
    }
    if ($SkipSeed) { $launcherArguments += "-SkipSeed" }
    if ($NoBrowser) { $launcherArguments += "-NoBrowser" }

    Invoke-LocalLauncher -Launcher $launcher -Arguments $launcherArguments

    if ($needsRebuild) {
        [IO.File]::WriteAllText(
            $imageRevisionFile,
            $targetRevision + [Environment]::NewLine,
            [Text.UTF8Encoding]::new($false)
        )
    }
}
catch {
    $failed = $true
    Write-Host ""
    Write-Host "FAILED:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
}
finally {
    if ($failed -and $PauseOnError -and -not $DryRun) {
        Write-Host ""
        Read-Host "Press Enter to close this window"
    }
}

if ($failed) {
    exit 1
}

exit 0
