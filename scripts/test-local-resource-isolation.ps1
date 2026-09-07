[CmdletBinding()]
param([string]$BackendImage = "aurum-pharma-e2e-backend:local")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$prefix = "aurum-local-isolation-" + [guid]::NewGuid().ToString("N").Substring(0, 12)
$scratch = Join-Path $workspace ".codex"
[void](New-Item -ItemType Directory -Path $scratch -Force)
$probePath = Join-Path $scratch "$prefix.py"
$targetPath = Join-Path $scratch "$prefix.json"
$containers = [Collections.Generic.List[string]]::new()
$networks = [Collections.Generic.List[string]]::new()
$roleZones = @{
    api = @("broker", "db", "storage", "web")
    beat = @("broker")
    system = @("broker", "db")
    catalog = @("broker", "db", "storage")
    billing = @("broker", "db")
    mailer = @("broker", "db", "mailer-egress")
}

function Invoke-TestDocker {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $output = & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Isolated resource Docker command failed (exit $LASTEXITCODE)." }
    return $output
}

function Assert-ComposeIsolation {
    param([string]$FileName)
    $model = Invoke-TestDocker -Arguments @(
        "compose", "--file", (Join-Path $workspace $FileName),
        "config", "--no-interpolate", "--format", "json"
    ) | ConvertFrom-Json -AsHashtable
    $resources = @{ postgres = "db"; redis = "broker"; minio = "storage" }
    if ($model.services.ContainsKey("prometheus")) { $resources.prometheus = "monitoring" }
    foreach ($serviceName in $resources.Keys) {
        $service = $model.services[$serviceName]
        $zone = $resources[$serviceName]
        if ($service.networks.Count -ne 1 -or -not $service.networks.ContainsKey($zone)) {
            throw "${FileName}: $serviceName must belong only to $zone."
        }
        $hasPublishedPorts = $service.ContainsKey("ports") -and @($service.ports).Count -gt 0
        if ($model.networks[$zone].internal -ne $true -or $hasPublishedPorts) {
            throw "${FileName}: $serviceName must be internal with no published ports."
        }
    }
    $roleServices = @{
        api = "backend"; beat = "celery-beat"; system = "celery-worker"
        catalog = "catalog-worker"; billing = "billing-worker"; mailer = "platform-mailer"
    }
    foreach ($role in $roleServices.Keys) {
        $expected = @($roleZones[$role])
        if ($role -eq "api" -and $FileName -eq "docker-compose.yml") { $expected += "monitoring" }
        $actual = @($model.services[$roleServices[$role]].networks.Keys)
        if (Compare-Object -ReferenceObject $expected -DifferenceObject $actual) {
            throw "${FileName}: $role network memberships do not match its resource boundary."
        }
    }
    Write-Host "${FileName}: internal resources, unpublished ports and exact role networks passed."
}

$probe = @'
import json
import socket
import socketserver
import sys
import threading
from pathlib import Path

if sys.argv[1] == "listener":
    for port in sys.argv[2:]:
        server = socketserver.ThreadingTCPServer(
            ("0.0.0.0", int(port)), socketserver.BaseRequestHandler
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Event().wait()
else:
    role = sys.argv[2]
    allowed = set(sys.argv[3:])
    targets = json.loads(Path("/probe/targets.json").read_text())
    counts = {"allowed": 0, "denied": 0}
    for target in targets:
        zone, port = target["zone"], target["port"]
        hosts = [("dns", target["dns"])]
        hosts.extend(("resource-ip", host) for host in target["ips"])
        for kind, host in hosts:
            connected = False
            try:
                socket.create_connection((host, port), timeout=0.5).close()
                connected = True
            except OSError:
                pass
            expected = zone in allowed
            if connected != expected:
                raise AssertionError(f"{role}:{zone}:{port}:{kind} unexpected reachability")
            counts["allowed" if expected else "denied"] += 1
            if kind == "dns" and not expected:
                try:
                    socket.getaddrinfo(host, port)
                except socket.gaierror:
                    pass
                else:
                    raise AssertionError(f"{role}:{zone} foreign resource DNS resolves")
    print(json.dumps({"role": role, **counts, "foreign_all_resource_ips": True}))
'@

try {
    Assert-ComposeIsolation -FileName "docker-compose.yml"
    Assert-ComposeIsolation -FileName "docker-compose.demo.yml"
    [IO.File]::WriteAllText($probePath, $probe.Replace("`r`n", "`n"), [Text.UTF8Encoding]::new($false))
    foreach ($zone in @("db", "broker", "storage", "mailer-egress", "web")) {
        $arguments = @("network", "create")
        if ($zone -in @("db", "broker", "storage")) { $arguments += "--internal" }
        $network = "$prefix-$zone"
        [void](Invoke-TestDocker -Arguments ($arguments + $network))
        $networks.Add($network)
    }
    $targets = @()
    $resourcePorts = @{ db = @(5432); broker = @(6379); storage = @(9000, 9001) }
    foreach ($zone in @("db", "broker", "storage")) {
        $name = "$prefix-$zone-resource"
        $arguments = @(
            "create", "--name", $name, "--network", "$prefix-$zone", "--network-alias", "$zone-probe",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--mount", "type=bind,source=$probePath,target=/probe/probe.py,readonly"
        )
        $arguments += @("--entrypoint", "python", $BackendImage, "/probe/probe.py", "listener")
        $arguments += @($resourcePorts[$zone] | ForEach-Object { [string]$_ })
        [void](Invoke-TestDocker -Arguments $arguments)
        $containers.Add($name)
        [void](Invoke-TestDocker -Arguments @("start", $name))
        $model = (Invoke-TestDocker -Arguments @("inspect", $name) | ConvertFrom-Json)[0]
        $ips = @($model.NetworkSettings.Networks.PSObject.Properties | ForEach-Object { $_.Value.IPAddress })
        if ($ips.Count -ne 1) { throw "Resource must belong only to its internal network." }
        $bindings = @()
        if ($null -ne $model.HostConfig.PortBindings) { $bindings = @($model.HostConfig.PortBindings.PSObject.Properties) }
        $published = @()
        if ($null -ne $model.NetworkSettings.Ports) {
            $published = @($model.NetworkSettings.Ports.PSObject.Properties | Where-Object { $null -ne $_.Value -and @($_.Value).Count -gt 0 })
        }
        if ($bindings.Count -gt 0 -or $published.Count -gt 0) { throw "Resource must have no host port bindings." }
        foreach ($port in $resourcePorts[$zone]) {
            $ready = $false
            for ($attempt = 0; $attempt -lt 15; $attempt++) {
                & docker exec $name python -c 'import socket,sys; socket.create_connection(("127.0.0.1",int(sys.argv[1])),timeout=1).close()' $port *> $null
                if ($LASTEXITCODE -eq 0) { $ready = $true; break }
                Start-Sleep -Seconds 1
            }
            if (-not $ready) { throw "Isolated $zone listener did not become ready." }
            $targets += @{
                zone = $zone; dns = "$zone-probe"; port = $port; ips = $ips
            }
        }
    }
    Write-Host "Actual resource containers: internal networks and no host port bindings passed."
    [IO.File]::WriteAllText($targetPath, (ConvertTo-Json -InputObject $targets -Depth 4), [Text.UTF8Encoding]::new($false))
    foreach ($role in @("api", "beat", "system", "catalog", "billing", "mailer")) {
        $name = "$prefix-$role-probe"
        $arguments = @(
            "create", "--name", $name, "--network", "$prefix-broker",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--mount", "type=bind,source=$probePath,target=/probe/probe.py,readonly",
            "--mount", "type=bind,source=$targetPath,target=/probe/targets.json,readonly",
            "--entrypoint", "python", $BackendImage, "/probe/probe.py", "check", $role
        ) + $roleZones[$role]
        [void](Invoke-TestDocker -Arguments $arguments)
        $containers.Add($name)
        foreach ($zone in $roleZones[$role] | Select-Object -Skip 1) {
            [void](Invoke-TestDocker -Arguments @("network", "connect", "$prefix-$zone", $name))
        }
        Invoke-TestDocker -Arguments @("start", "--attach", $name)
        $state = (Invoke-TestDocker -Arguments @("inspect", $name) | ConvertFrom-Json)[0].State
        if ($state.ExitCode -ne 0) { throw "Local resource boundary probe failed for $role." }
    }
    Write-Host "Local resource isolation passed: no host bindings; 6 role profiles; foreign DNS and all resource IPs denied."
} finally {
    foreach ($name in $containers) { & docker rm --force $name *> $null }
    foreach ($name in $networks) { & docker network rm $name *> $null }
    foreach ($path in @($probePath, $targetPath)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
    }
}
