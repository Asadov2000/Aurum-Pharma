[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "demo-broker-rollout.ps1")

$script:assertions = 0
function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:assertions++
}

function Invoke-RolloutScenario {
    param(
        [string[]]$Counts = @("0", "0"),
        [switch]$FailWorkerStop,
        [switch]$Fresh,
        [switch]$RedisStopped
    )
    $state = @{
        Calls = [System.Collections.Generic.List[string]]::new()
        Running = @("aurum-demo-backend", "aurum-demo-celery-beat", "aurum-demo-catalog-worker")
        Counts = $Counts
        FailWorkerStop = [bool]$FailWorkerStop
        Failure = $null
    }
    if (-not $RedisStopped) { $state.Running += "aurum-demo-redis" }
    $state.Inventory = @($state.Running + "aurum-demo-redis" | Select-Object -Unique)
    if ($Fresh) { $state.Running = @(); $state.Inventory = @() }
    $runner = {
        param([string[]]$DockerArguments)
        $state.Calls.Add(($DockerArguments -join " "))
        switch ($DockerArguments[0]) {
            "ps" {
                if ($DockerArguments -contains "--all") { $state.Inventory }
                else { $state.Running }
            }
            "stop" {
                if ($state.FailWorkerStop -and $DockerArguments -contains "aurum-demo-catalog-worker") {
                    throw "Injected stop failure"
                }
                $state.Running = @($state.Running | Where-Object { $DockerArguments -notcontains $_ })
            }
            "start" { $state.Running = @($state.Running + $DockerArguments[1..($DockerArguments.Count - 1)] | Select-Object -Unique) }
            "exec" { $state.Counts }
            default { throw "Unexpected Docker operation" }
        }
    }.GetNewClosure()
    try { Stop-DemoBrokerClientsForUpgrade -Docker $runner }
    catch { $state.Failure = $_.Exception.Message }
    return $state
}

$success = Invoke-RolloutScenario
Assert-True ($null -eq $success.Failure) "Empty tracking should permit upgrade"
Assert-True ($success.Calls[2] -like "stop --time 30 *backend*beat*") "Producers must stop first"
Assert-True ($success.Calls[3] -like "stop --time 180 *catalog-worker*") "Workers need graceful shutdown"
Assert-True ($success.Calls[5] -like "exec aurum-demo-redis*") "Inspect tracking only after clients stopped"
Assert-True ($success.Running.Count -eq 1) "Only Redis should remain running"

foreach ($counts in @(@("1", "0"), @("0", "1"), @("NOAUTH"), @())) {
    $blocked = Invoke-RolloutScenario -Counts $counts
    Assert-True ($null -ne $blocked.Failure) "Nonempty or unreadable tracking must block"
    Assert-True ($blocked.Calls[-1] -like "start *catalog-worker*backend*beat*") "Failure must restore previous workers then producers"
}
$stopped = Invoke-RolloutScenario -FailWorkerStop
Assert-True ($stopped.Failure -eq "Injected stop failure") "Stop failure must propagate"
Assert-True (@($stopped.Calls | Where-Object { $_ -like "exec *" }).Count -eq 0) "Do not probe after failed stop"
Assert-True ($stopped.Running -contains "aurum-demo-backend") "Partial stop must restore backend"
$fresh = Invoke-RolloutScenario -Fresh
Assert-True ($fresh.Calls.Count -eq 2 -and $null -eq $fresh.Failure) "Fresh stack needs inventory only"
$cold = Invoke-RolloutScenario -RedisStopped
Assert-True ($null -eq $cold.Failure -and $cold.Calls[5] -eq "start aurum-demo-redis") "Inspect stopped Redis using the existing container"
Assert-True (-not (($success.Calls -join " ") -match "(?i)flush|down|\brm\b")) "Rollout must never clear data"
Write-Host "Demo broker rollout: $script:assertions assertions passed."
