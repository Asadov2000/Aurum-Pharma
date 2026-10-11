<#
.SYNOPSIS
Run locked backend checks inside a fresh, isolated Docker Compose project.
.DESCRIPTION
No host Poetry, dotenv, existing database, or published ports are used. The
public verification credentials belong only to this invocation's containers.
PlanOnly is read-only. Dot-sourcing declares functions without executing checks.
.EXAMPLE
pwsh -File scripts/backend-isolated.ps1 -Check Tests tests/domains/auth -q --tb=short
.EXAMPLE
pwsh -File scripts/backend-isolated.ps1 -Check Static
#>
[CmdletBinding()]
param(
    [ValidateSet('Tests', 'Ruff', 'Black', 'Mypy', 'Static', 'All')]
    [string]$Check = 'All',
    [switch]$PlanOnly,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs = @('tests', '-q', '--tb=short')
)

. (Join-Path $PSScriptRoot 'backend-isolated-guards.ps1')

function Get-BackendVerifyPlan {
    param([string]$SelectedCheck, [string[]]$TestArguments)
    $workspace = Split-Path $PSScriptRoot -Parent
    $project = 'aurum-verify-' + [Guid]::NewGuid().ToString('N')
    $commands = @()
    if ($SelectedCheck -in @('All', 'Static', 'Ruff')) { $commands += ,@('-m', 'ruff', 'check', 'app', 'tests') }
    if ($SelectedCheck -in @('All', 'Static', 'Black')) { $commands += ,@('-m', 'black', '--check', 'app', 'tests') }
    if ($SelectedCheck -in @('All', 'Static', 'Mypy')) { $commands += ,@('-m', 'mypy', 'app') }
    if ($SelectedCheck -in @('All', 'Tests')) { $commands += ,(@('-m', 'pytest') + $TestArguments) }
    return [pscustomobject][ordered]@{
        check = $SelectedCheck
        project = $project
        compose_file = Join-Path $workspace 'docker-compose.verify.yml'
        database = 'aurum_verify_test'
        services = @(if ($SelectedCheck -in @('All', 'Tests')) { 'postgres'; 'redis'; 'minio' })
        commands = $commands
        image = "$project-tools"
        published_ports = $false
        host_environment = $false
        source_mounts = 'selected-read-only'
        cleanup = 'project-label-checked'
    }
}

function Invoke-BackendVerifyDocker {
    param([string[]]$Arguments, [switch]$Capture)
    # Commands use argument arrays, never a generated shell command. stderr remains
    # visible and cannot become inspection JSON, including under Windows PS 5.1.
    if ($Capture) {
        $output = @(& docker @Arguments)
        $code = $LASTEXITCODE
        if ($code -ne 0) { throw "Docker inspection failed (exit $code)." }
        return ($output -join "`n")
    }
    & docker @Arguments | Out-Host
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        $failure = New-Object System.Exception("Backend verification command failed (exit $code).")
        $failure.Data['ExitCode'] = $code
        throw $failure
    }
}

function Remove-BackendVerifyProject {
    param([string]$ProjectName, [string[]]$ComposeArguments)
    Assert-BackendVerifyProjectName $ProjectName
    # Inventory and inspect every resource before deleting any. Never trust name
    # matching alone and never remove unrelated/orphan resources or cached images.
    foreach ($kind in @('container', 'network', 'volume')) {
        $listArgs = if ($kind -eq 'container') { @('ps', '-a', '-q') } else { @($kind, 'ls', '-q') }
        $rawIds = Invoke-BackendVerifyDocker -Arguments ($listArgs + @(
            '--filter', "label=com.docker.compose.project=$ProjectName"
        )) -Capture
        foreach ($resourceId in @($rawIds -split '\r?\n' | Where-Object { $_ })) {
            $json = Invoke-BackendVerifyDocker -Arguments @($kind, 'inspect', $resourceId) -Capture
            $resources = @($json | ConvertFrom-Json)
            if ($resources.Count -ne 1) { throw 'Ambiguous verification resource inspection.' }
            $resource = $resources[0]
            $labels = if ($kind -eq 'container') { $resource.Config.Labels } else { $resource.Labels }
            Assert-BackendVerifyResourceOwnership -ProjectName $ProjectName `
                -ActualProject $labels.'com.docker.compose.project' -ResourceName $resource.Name
        }
    }
    # Explicit build labels work across Compose/Bake versions. Check them before any cleanup, then
    # remove only this invocation's tag without --force; shared cache is retained.
    $imageName = "$ProjectName-tools"
    $imageIds = Invoke-BackendVerifyDocker -Arguments @('image', 'ls', '-q', '--filter', "reference=$imageName") -Capture
    if ($imageIds.Trim()) {
        $imageJson = Invoke-BackendVerifyDocker -Arguments @('image', 'inspect', $imageName) -Capture
        $images = @($imageJson | ConvertFrom-Json)
        if ($images.Count -ne 1) { throw 'Ambiguous verification image inspection.' }
        Assert-BackendVerifyResourceOwnership -ProjectName $ProjectName `
            -ActualProject $images[0].Config.Labels.'tj.aurum.verify.project' -ResourceName $imageName
        if ($images[0].Config.Labels.'tj.aurum.verify.service' -cne 'tools') {
            throw 'Refusing cleanup of an image not built for verification tools.'
        }
    }
    Invoke-BackendVerifyDocker -Arguments ($ComposeArguments + @('down', '--volumes', '--timeout', '10'))
    if ($imageIds.Trim()) { Invoke-BackendVerifyDocker -Arguments @('image', 'rm', $imageName) }
}

function Invoke-BackendIsolated {
    param([string]$SelectedCheck, [string[]]$TestArguments, [switch]$ShowPlan)
    $plan = Get-BackendVerifyPlan -SelectedCheck $SelectedCheck -TestArguments $TestArguments
    Assert-BackendVerifyProjectName $plan.project
    if ($ShowPlan) {
        $plan | ConvertTo-Json -Depth 6
        return
    }
    $ErrorActionPreference = 'Stop'
    # PowerShell 7 may turn native nonzero exits into exceptions before the exact
    # exit code is captured. The wrapper handles those consistently with PS 5.1.
    $PSNativeCommandUseErrorActionPreference = $false
    $nullDevice = if ([IO.Path]::DirectorySeparatorChar -eq '\') { 'NUL' } else { '/dev/null' }
    $compose = @('compose', '--env-file', $nullDevice, '--project-name', $plan.project,
        '--project-directory', (Split-Path $plan.compose_file -Parent), '-f', $plan.compose_file)
    # Compose control variables must not import env files, override profiles or
    # reuse an external project. They are restored even on a failed check.
    $savedEnvironment = @{}
    foreach ($entry in @(Get-ChildItem Env: | Where-Object { $_.Name -like 'COMPOSE_*' })) {
        $savedEnvironment[$entry.Name] = $entry.Value
        [Environment]::SetEnvironmentVariable($entry.Name, $null, 'Process')
    }
    $failure = $null
    $cleanupFailure = $null
    $mayHaveResources = $false
    try {
        Write-Host "Backend verification project: $($plan.project)"
        $mayHaveResources = $true
        # Compose/Bake does not consistently label built images. Own labels must
        # be explicit so a cached build cannot lose its cleanup ownership proof.
        $backendDirectory = Join-Path (Split-Path $plan.compose_file -Parent) 'backend'
        Invoke-BackendVerifyDocker -Arguments @('build', '--file', (Join-Path $backendDirectory 'Dockerfile'),
            '--tag', $plan.image, '--label', "tj.aurum.verify.project=$($plan.project)",
            '--label', 'tj.aurum.verify.service=tools', $backendDirectory)
        # Fail on inexpensive static checks before starting disposable services.
        # Static and All reuse this invocation's one tools image and cleanup.
        foreach ($command in $plan.commands) {
            if ($command[1] -ne 'pytest') {
                Invoke-BackendVerifyDocker -Arguments ($compose + @('run', '--rm', '--no-deps', 'tools') + $command)
            }
        }
        if ($plan.services.Count -gt 0) {
            Invoke-BackendVerifyDocker -Arguments ($compose + @('up', '-d', '--wait', '--wait-timeout', '180') + $plan.services)
            # init.sh provisions extensions and initial roles. The maintained
            # bootstrap/migration sequence handles historical owner transitions.
            Invoke-BackendVerifyDocker -Arguments ($compose + @('run', '--rm', '--no-deps', 'bootstrap'))
            Invoke-BackendVerifyDocker -Arguments ($compose + @('run', '--rm', '--no-deps', 'tools', '-m', 'app.migrate', 'legacy-upgrade', '0029'))
            Invoke-BackendVerifyDocker -Arguments ($compose + @('run', '--rm', '--no-deps', 'bootstrap'))
            Invoke-BackendVerifyDocker -Arguments ($compose + @('run', '--rm', '--no-deps',
                '--env', 'POSTGRES_USER=postgres', '--env', 'POSTGRES_PASSWORD=verify_postgres_only',
                '--env', 'MIGRATION_DB_HOST=postgres', '--env', 'MIGRATION_DB_NAME=aurum_verify_test',
                'tools', '-m', 'app.db_owner_migrate'))
            Invoke-BackendVerifyDocker -Arguments ($compose + @('run', '--rm', '--no-deps', 'bootstrap'))
            Invoke-BackendVerifyDocker -Arguments ($compose + @('run', '--rm', '--no-deps', 'tools', '-m', 'app.migrate', 'upgrade', 'head'))
            $storageReady = @'
import time
from minio import Minio
from urllib3 import PoolManager, Timeout
client = Minio('minio:9000', access_key='verify_minio', secret_key='verify_minio_only', secure=False,
               http_client=PoolManager(timeout=Timeout(connect=2, read=2), retries=False))
for attempt in range(30):
    try:
        if not client.bucket_exists('aurum'):
            client.make_bucket('aurum')
        break
    except Exception:
        if attempt == 29:
            raise SystemExit('Isolated verification storage did not become ready.')
        time.sleep(1)
'@
            Invoke-BackendVerifyDocker -Arguments ($compose + @('run', '--rm', '--no-deps', 'tools', '-c', $storageReady))
        }
        foreach ($command in $plan.commands) {
            if ($command[1] -eq 'pytest') {
                Invoke-BackendVerifyDocker -Arguments ($compose + @('run', '--rm', '--no-deps', 'tools') + $command)
            }
        }
    }
    catch { $failure = $_ }
    finally {
        try {
            if ($mayHaveResources) {
                Remove-BackendVerifyProject -ProjectName $plan.project -ComposeArguments $compose
            }
        }
        catch { $cleanupFailure = $_ }
        foreach ($name in $savedEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
        }
    }
    if ($cleanupFailure) { Write-Warning "Verification cleanup could not be confirmed; inspect project $($plan.project)." }
    if ($failure) { throw $failure }
    if ($cleanupFailure) { throw $cleanupFailure }
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        Invoke-BackendIsolated -SelectedCheck $Check -TestArguments $PytestArgs -ShowPlan:$PlanOnly
    }
    catch {
        Write-Error -Message $_.Exception.Message -ErrorAction Continue
        $exitCode = 1
        if ($_.Exception.Data.Contains('ExitCode')) { $exitCode = [int]$_.Exception.Data['ExitCode'] }
        exit $exitCode
    }
}
