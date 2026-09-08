[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "backend-isolated-guards.ps1")
$runner = Join-Path $PSScriptRoot "backend-isolated.ps1"
$script:assertions = 0

function Assert-True {
    param([bool]$Condition, [string]$Because)
    $script:assertions++
    if (-not $Condition) { throw "Backend isolation assertion failed: $Because" }
}

function Assert-Rejected {
    param([scriptblock]$Action, [string]$Because)
    $rejected = $false
    try { & $Action | Out-Null } catch { $rejected = $true }
    Assert-True -Condition $rejected -Because $Because
}

$project = "aurum-verify-" + [Guid]::NewGuid().ToString("N")
$otherProject = "aurum-verify-" + [Guid]::NewGuid().ToString("N")
Assert-BackendVerifyProjectName -ProjectName $project
Assert-BackendVerifyResourceOwnership -ProjectName $project -ActualProject $project -ResourceName "${project}_postgres-data"
Assert-True -Condition $true -Because "a uniquely named, labelled project owns its resource"

foreach ($invalid in @("", "aurum", "aurum-dev", "aurum-main", "aurum-verify", "aurum-verify-1234", "$project-extra", $project.ToUpperInvariant())) {
    Assert-Rejected -Action { Assert-BackendVerifyProjectName -ProjectName $invalid } `
        -Because "shared, malformed or ambiguous project names are rejected"
}
foreach ($actualProject in @("", "aurum", "aurum-dev", $otherProject)) {
    Assert-Rejected -Action {
        Assert-BackendVerifyResourceOwnership -ProjectName $project -ActualProject $actualProject -ResourceName "${project}_postgres-data"
    } -Because "a matching name cannot override a missing or foreign ownership label"
}
foreach ($resource in @("aurum_postgres_data", "${otherProject}_postgres-data", "prefix-${project}_postgres-data", "${project}other_postgres-data")) {
    Assert-Rejected -Action {
        Assert-BackendVerifyResourceOwnership -ProjectName $project -ActualProject $project -ResourceName $resource
    } -Because "a matching label cannot authorize a shared or foreign resource name"
}

$firstPlan = & {
    function docker { throw "PlanOnly attempted to execute Docker." }
    function poetry { throw "PlanOnly attempted to execute Poetry." }
    $oldProject = $env:COMPOSE_PROJECT_NAME
    $oldComposeFile = $env:COMPOSE_FILE
    $oldDatabase = $env:DATABASE_URL
    try {
        $env:COMPOSE_PROJECT_NAME = "aurum-dev"
        $env:COMPOSE_FILE = "docker-compose.yml"
        $env:DATABASE_URL = "postgresql://fixture.invalid/shared"
        $json = & $runner -Check Tests -PlanOnly tests/domains/catalog tests/isolation/test_catalog_rls.py -q --tb=short
        ($json -join [Environment]::NewLine) | ConvertFrom-Json
    }
    finally {
        $env:COMPOSE_PROJECT_NAME = $oldProject
        $env:COMPOSE_FILE = $oldComposeFile
        $env:DATABASE_URL = $oldDatabase
    }
}
$secondJson = & $runner -Check Ruff -PlanOnly
$secondPlan = ($secondJson -join [Environment]::NewLine) | ConvertFrom-Json
Assert-BackendVerifyProjectName -ProjectName $firstPlan.project
Assert-BackendVerifyProjectName -ProjectName $secondPlan.project
Assert-True -Condition ($firstPlan.project -cne $secondPlan.project) -Because "separate invocations cannot share a Compose project"
Assert-True -Condition ($firstPlan.database -ceq "aurum_verify_test") -Because "host DATABASE_URL cannot choose the test database"
Assert-True -Condition ($firstPlan.host_environment -eq $false) -Because "the plan excludes host credentials and dotenv"
Assert-True -Condition ($firstPlan.published_ports -eq $false) -Because "isolated services cannot collide with demo ports"
Assert-True -Condition ($firstPlan.compose_file -ceq (Join-Path (Split-Path $PSScriptRoot -Parent) "docker-compose.verify.yml")) -Because "COMPOSE_FILE cannot select the shared stack"
$pytestCommand = @($firstPlan.commands[0])
Assert-True -Condition (($pytestCommand -join "|") -ceq "-m|pytest|tests/domains/catalog|tests/isolation/test_catalog_rls.py|-q|--tb=short") -Because "remaining arguments preserve multiple selected targets and pytest options"
Assert-True -Condition (@($secondPlan.services).Count -eq 0) -Because "a static Ruff check does not start databases or queues"

$shellArguments = @('-NoProfile')
if ($env:OS -eq 'Windows_NT') { $shellArguments += @('-ExecutionPolicy', 'Bypass') }
$shellArguments += @('-File', $runner, '-Check', 'Tests', '-PlanOnly',
    'tests/domains/catalog', 'tests/isolation/test_catalog_rls.py', '-q', '--tb=short')
$externalJson = & (Get-Process -Id $PID).Path @shellArguments
Assert-True -Condition ($LASTEXITCODE -eq 0) -Because "the actual -File entrypoint accepts multiple targets and options"
$externalPlan = ($externalJson -join [Environment]::NewLine) | ConvertFrom-Json
Assert-True -Condition ((@($externalPlan.commands[0]) -join '|') -ceq ($pytestCommand -join '|')) -Because "external PowerShell binding preserves the complete pytest command"

# Load the actual command/cleanup functions, replacing only the external Docker boundary.
. $runner
& {
    function docker { $global:LASTEXITCODE = 23 }
    $savedExitCode = Get-Variable -Name LASTEXITCODE -ValueOnly -ErrorAction SilentlyContinue
    $code = 0
    try { Invoke-BackendVerifyDocker -Arguments @('fixture-command') }
    catch { $code = $_.Exception.Data['ExitCode'] }
    finally { $global:LASTEXITCODE = $savedExitCode }
    Assert-True -Condition ($code -eq 23) -Because "a failed native command retains its exact nonzero exit code"
}

& {
    $script:fixtureCalls = New-Object 'System.Collections.Generic.List[object]'
    function Invoke-BackendVerifyDocker {
        param([string[]]$Arguments, [switch]$Capture)
        $script:fixtureCalls.Add(@($Arguments))
        if ($Arguments -contains 'inspect') {
            return ConvertTo-Json -Depth 4 -InputObject @(@{
                Name = "${project}-postgres-1"
                Config = @{ Labels = @{ 'com.docker.compose.project' = 'aurum-dev' } }
            })
        }
        if ($Capture) { return 'fixture-id' }
    }
    Assert-Rejected -Action {
        Remove-BackendVerifyProject -ProjectName $project -ComposeArguments @('compose', '--project-name', $project)
    } -Because "cleanup inspects actual labels before deleting a resource"
    Assert-True -Condition (@($script:fixtureCalls | Where-Object { $_ -contains 'down' }).Count -eq 0) -Because "foreign ownership prevents every destructive cleanup call"
}

foreach ($imageOwner in @($project, $otherProject)) {
    & {
        $script:fixtureCalls = New-Object 'System.Collections.Generic.List[object]'
        function Invoke-BackendVerifyDocker {
            param([string[]]$Arguments, [switch]$Capture)
            $script:fixtureCalls.Add(@($Arguments))
            if ($Arguments[0] -eq 'image' -and $Arguments[1] -eq 'ls') { return 'fixture-image-id' }
            if ($Arguments[0] -eq 'image' -and $Arguments[1] -eq 'inspect') {
                return ConvertTo-Json -Depth 4 -InputObject @(@{
                    Config = @{ Labels = @{
                        'com.docker.compose.project' = $imageOwner
                        'com.docker.compose.service' = 'tools'
                    } }
                })
            }
            if ($Capture) { return '' }
        }
        $cleanup = { Remove-BackendVerifyProject -ProjectName $project -ComposeArguments @('compose', '--project-name', $project) }
        if ($imageOwner -ceq $project) {
            & $cleanup
            $removeCalls = @($script:fixtureCalls | Where-Object { $_ -contains 'rm' })
            Assert-True -Condition ($removeCalls.Count -eq 1) -Because "cleanup removes one owned image tag"
            Assert-True -Condition (($removeCalls[0] -join '|') -ceq "image|rm|${project}-tools") -Because "cleanup cannot force-delete shared image tags"
        }
        else {
            Assert-Rejected -Action $cleanup -Because "an image with a foreign ownership label blocks cleanup"
            Assert-True -Condition (@($script:fixtureCalls | Where-Object { $_ -contains 'down' -or $_ -contains 'rm' }).Count -eq 0) -Because "image ownership is checked before deleting any resources"
        }
    }
}

& {
    $script:fixtureCalls = New-Object 'System.Collections.Generic.List[object]'
    function Invoke-BackendVerifyDocker {
        param([string[]]$Arguments, [switch]$Capture)
        Assert-True -Condition ([string]::IsNullOrEmpty($env:COMPOSE_FILE)) -Because "host Compose overrides are removed before execution"
        $script:fixtureCalls.Add(@($Arguments))
        if ($Capture) { return '' }
        if ($Arguments -contains 'ruff') {
            $failure = New-Object System.Exception('Fixture check failure')
            $failure.Data['ExitCode'] = 29
            throw $failure
        }
    }
    $oldComposeFile = $env:COMPOSE_FILE
    $env:COMPOSE_FILE = 'shared-dev-fixture.yml'
    $code = 0
    try {
        try { Invoke-BackendIsolated -SelectedCheck Ruff -TestArguments @() }
        catch {
            if (-not $_.Exception.Data.Contains('ExitCode')) { throw }
            $code = $_.Exception.Data['ExitCode']
        }
        Assert-True -Condition ($env:COMPOSE_FILE -ceq 'shared-dev-fixture.yml') -Because "a failed check restores the caller's environment"
    }
    finally { $env:COMPOSE_FILE = $oldComposeFile }
    Assert-True -Condition ($code -eq 29) -Because "cleanup does not turn a failed check into success"
    $downs = @($script:fixtureCalls | Where-Object { $_ -contains 'down' })
    Assert-True -Condition ($downs.Count -eq 1) -Because "a failed check still cleans up its own project once"
    $build = @($script:fixtureCalls[0])
    $projectIndex = [Array]::IndexOf($build, '--project-name')
    $ownedProject = $build[$projectIndex + 1]
    Assert-BackendVerifyProjectName -ProjectName $ownedProject
    Assert-True -Condition ($downs[0] -contains $ownedProject) -Because "cleanup uses exactly the project created by this invocation"
    $envIndex = [Array]::IndexOf($build, '--env-file')
    $nullDevice = if ($env:OS -eq 'Windows_NT') { 'NUL' } else { '/dev/null' }
    Assert-True -Condition ($envIndex -ge 0 -and $build[$envIndex + 1] -ceq $nullDevice) -Because "Compose receives an explicit empty env file"
}

Write-Host "Backend isolation self-test passed: $script:assertions assertions." -ForegroundColor Green
