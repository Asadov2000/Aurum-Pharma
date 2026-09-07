Set-StrictMode -Version Latest

function Stop-DemoBrokerClientsForUpgrade {
    [CmdletBinding()]
    param(
        [switch]$DryRun,
        # Injectable command runner keeps rollback and ordering tests independent of Docker.
        [scriptblock]$Docker = {
            param([string[]]$DockerArguments)
            $output = @(& docker @DockerArguments 2>$null)
            if ($LASTEXITCODE -ne 0) {
                throw "Broker rollout Docker operation failed; existing data was preserved."
            }
            $output
        }
    )

    if ($DryRun) {
        Write-Host "Stop demo producers, gracefully stop workers, then require empty legacy delivery tracking."
        Write-Host "Pending queue messages are preserved; failed checks restart the previous containers."
        return
    }

    $producers = @("aurum-demo-backend", "aurum-demo-celery-beat")
    $workers = @(
        "aurum-demo-celery-worker", "aurum-demo-catalog-worker",
        "aurum-demo-billing-worker", "aurum-demo-platform-mailer"
    )
    $redisName = "aurum-demo-redis"
    $inventory = @(& $Docker -DockerArguments @("ps", "--all", "--format", "{{.Names}}"))
    $running = @(& $Docker -DockerArguments @("ps", "--format", "{{.Names}}"))
    $activeProducers = @($producers | Where-Object { $running -contains $_ })
    $activeWorkers = @($workers | Where-Object { $running -contains $_ })
    $previousClients = @($activeWorkers + $activeProducers)

    if ($inventory -notcontains $redisName) {
        if ($previousClients.Count -gt 0) {
            throw "Demo clients exist without their Redis container; refusing an ambiguous broker upgrade."
        }
        return
    }

    $startedRedis = $false
    try {
        # Stop enqueueing first. A 180-second worker grace exceeds the catalog hard limit.
        if ($activeProducers.Count -gt 0) {
            $null = & $Docker -DockerArguments (@("stop", "--time", "30") + $activeProducers)
        }
        if ($activeWorkers.Count -gt 0) {
            $null = & $Docker -DockerArguments (@("stop", "--time", "180") + $activeWorkers)
        }
        $stillRunning = @(& $Docker -DockerArguments @("ps", "--format", "{{.Names}}"))
        if (@(($producers + $workers) | Where-Object { $stillRunning -contains $_ }).Count -gt 0) {
            throw "Demo task clients are still running; refusing to inspect moving delivery records."
        }
        if ($stillRunning -notcontains $redisName) {
            # Start the existing container, never recreate its volume or configuration here.
            $null = & $Docker -DockerArguments @("start", $redisName)
            $startedRedis = $true
        }
        $probe = @'
set -eu
export REDISCLI_AUTH="$REDIS_PASSWORD"
attempt=0
until redis-cli --raw ping | grep -qx PONG; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 10 ] || exit 1
  sleep 1
done
redis-cli --raw HLEN unacked
redis-cli --raw ZCARD unacked_index
'@
        $counts = @(& $Docker -DockerArguments @(
            "exec", $redisName, "/bin/sh", "-ec", $probe.Replace("`r`n", "`n")
        ))
        if ($counts.Count -ne 2 -or $counts[0] -ne "0" -or $counts[1] -ne "0") {
            throw (
                "Legacy broker deliveries remain or could not be verified. " +
                "The previous workers must finish or restore them before retrying; no queue was cleared."
            )
        }
        Write-Host "Legacy broker deliveries: 0; producers and workers stopped. Pending queues preserved."
    }
    catch {
        $failure = $_
        try {
            # Container names still refer to the previous image: Compose has not recreated anything.
            if ($previousClients.Count -gt 0) {
                $null = & $Docker -DockerArguments (@("start") + $previousClients)
            }
            if ($startedRedis) {
                $null = & $Docker -DockerArguments @("stop", "--time", "30", $redisName)
            }
        }
        catch {
            throw "Broker upgrade aborted and previous containers could not all be restored. Data is preserved; inspect demo container status."
        }
        throw $failure
    }
}
