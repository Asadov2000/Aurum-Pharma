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
$staticPlan = & {
    function docker { throw "Static PlanOnly attempted to execute Docker." }
    $json = & $runner -Check Static -PlanOnly
    ($json -join [Environment]::NewLine) | ConvertFrom-Json
}
Assert-True -Condition (@($staticPlan.services).Count -eq 0) -Because "Static plans no database, queue or storage services"
$staticCommands = @($staticPlan.commands | ForEach-Object { $_ -join '|' })
Assert-True -Condition (($staticCommands -join ';') -ceq '-m|ruff|check|app|tests;-m|black|--check|app|tests;-m|mypy|app') -Because "Static plans all three unchanged quality checks in order"

$shellArguments = @('-NoProfile')
if ($env:OS -eq 'Windows_NT') { $shellArguments += @('-ExecutionPolicy', 'Bypass') }
$shellArguments += @('-File', $runner, '-Check', 'Tests', '-PlanOnly',
    'tests/domains/catalog', 'tests/isolation/test_catalog_rls.py', '-q', '--tb=short')
$powershellName = if ($PSEdition -eq "Desktop") { "powershell.exe" }
elseif ($env:OS -eq "Windows_NT") { "pwsh.exe" }
else { "pwsh" }
$powershellExecutable = Join-Path $PSHOME $powershellName
$externalJson = & $powershellExecutable @shellArguments
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

foreach ($imageCase in @(
    @{ Allowed = $true; Labels = @{ 'tj.aurum.verify.project' = $project; 'tj.aurum.verify.service' = 'tools' } },
    @{ Allowed = $false; Labels = @{ 'tj.aurum.verify.project' = $otherProject; 'tj.aurum.verify.service' = 'tools' } },
    @{ Allowed = $false; Labels = @{ 'tj.aurum.verify.project' = $project; 'tj.aurum.verify.service' = 'backend' } },
    @{ Allowed = $false; Labels = @{ 'tj.aurum.verify.service' = 'tools' } },
    @{ Allowed = $false; Labels = @{ 'tj.aurum.verify.project' = $project } },
    @{ Allowed = $false; Labels = @{ 'com.docker.compose.project' = $project; 'com.docker.compose.service' = 'tools' } }
)) {
    & {
        $script:fixtureCalls = New-Object 'System.Collections.Generic.List[object]'
        function Invoke-BackendVerifyDocker {
            param([string[]]$Arguments, [switch]$Capture)
            $script:fixtureCalls.Add(@($Arguments))
            if ($Arguments[0] -eq 'image' -and $Arguments[1] -eq 'ls') { return 'fixture-image-id' }
            if ($Arguments[0] -eq 'image' -and $Arguments[1] -eq 'inspect') {
                return ConvertTo-Json -Depth 4 -InputObject @(@{
                    Config = @{ Labels = $imageCase.Labels }
                })
            }
            if ($Capture) { return '' }
        }
        $cleanup = { Remove-BackendVerifyProject -ProjectName $project -ComposeArguments @('compose', '--project-name', $project) }
        if ($imageCase.Allowed) {
            & $cleanup
            $removeCalls = @($script:fixtureCalls | Where-Object { $_ -contains 'rm' })
            Assert-True -Condition ($removeCalls.Count -eq 1) -Because "own image labels authorize cleanup without automatic Compose labels"
            Assert-True -Condition (($removeCalls[0] -join '|') -ceq "image|rm|${project}-tools") -Because "cleanup cannot force-delete shared image tags"
        }
        else {
            Assert-Rejected -Action $cleanup -Because "missing or foreign own project/service labels block image cleanup"
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
    Assert-True -Condition ($build[0] -ceq 'build') -Because "the tools image is built directly with Docker"
    $compose = @($script:fixtureCalls | Where-Object { $_[0] -ceq 'compose' })[0]
    $projectIndex = [Array]::IndexOf($compose, '--project-name')
    Assert-True -Condition ($projectIndex -ge 0) -Because "Compose is bound to one explicit project"
    $ownedProject = $compose[$projectIndex + 1]
    Assert-BackendVerifyProjectName -ProjectName $ownedProject
    Assert-True -Condition ($downs[0] -contains $ownedProject) -Because "cleanup uses exactly the project created by this invocation"
    $backendRoot = Join-Path (Split-Path $PSScriptRoot -Parent) 'backend'
    foreach ($expectedArgument in @(
        @{ Flag = '--file'; Value = (Join-Path $backendRoot 'Dockerfile') },
        @{ Flag = '--tag'; Value = "${ownedProject}-tools" }
    )) {
        $argumentIndex = [Array]::IndexOf($build, $expectedArgument.Flag)
        Assert-True -Condition ($argumentIndex -ge 0 -and $build[$argumentIndex + 1] -ceq $expectedArgument.Value) -Because "the direct build selects this workspace Dockerfile and invocation image tag"
    }
    foreach ($label in @("tj.aurum.verify.project=$ownedProject", 'tj.aurum.verify.service=tools')) {
        $labelIndex = [Array]::IndexOf($build, $label)
        Assert-True -Condition ($labelIndex -gt 0 -and $build[$labelIndex - 1] -ceq '--label') -Because "the built image receives explicit ownership labels"
    }
    Assert-True -Condition ($build[-1] -ceq $backendRoot) -Because "the direct build uses only this workspace backend context"
    $envIndex = [Array]::IndexOf($compose, '--env-file')
    $nullDevice = if ($env:OS -eq 'Windows_NT') { 'NUL' } else { '/dev/null' }
    Assert-True -Condition ($envIndex -ge 0 -and $compose[$envIndex + 1] -ceq $nullDevice) -Because "Compose receives an explicit empty env file"
}

foreach ($batchCase in @(
    @{ Check = 'Static'; Failure = ''; Expected = 'ruff|black|mypy' },
    @{ Check = 'Static'; Failure = 'ruff'; Expected = 'ruff' },
    @{ Check = 'Static'; Failure = 'black'; Expected = 'ruff|black' },
    @{ Check = 'Static'; Failure = 'mypy'; Expected = 'ruff|black|mypy' },
    @{ Check = 'All'; Failure = 'black'; Expected = 'ruff|black' },
    @{ Check = 'All'; Failure = ''; Expected = 'ruff|black|mypy|pytest' }
)) {
    & {
        $script:fixtureCalls = New-Object 'System.Collections.Generic.List[object]'
        function Invoke-BackendVerifyDocker {
            param([string[]]$Arguments, [switch]$Capture)
            Assert-True -Condition ([string]::IsNullOrEmpty($env:COMPOSE_FILE)) -Because "batched checks clear host Compose overrides"
            $script:fixtureCalls.Add(@($Arguments))
            if ($Capture) { return '' }
            if ($batchCase.Failure -and $Arguments -contains $batchCase.Failure) {
                $failure = New-Object System.Exception('Fixture batch failure')
                $failure.Data['ExitCode'] = 37
                throw $failure
            }
        }
        $oldComposeFile = $env:COMPOSE_FILE
        $env:COMPOSE_FILE = 'shared-batch-fixture.yml'
        $code = 0
        try {
            try { Invoke-BackendIsolated -SelectedCheck $batchCase.Check -TestArguments @('tests', '-q') }
            catch {
                if (-not $_.Exception.Data.Contains('ExitCode')) { throw }
                $code = $_.Exception.Data['ExitCode']
            }
            Assert-True -Condition ($env:COMPOSE_FILE -ceq 'shared-batch-fixture.yml') -Because "batch success and failures restore caller Compose configuration"
        }
        finally { $env:COMPOSE_FILE = $oldComposeFile }
        $expectedCode = if ($batchCase.Failure) { 37 } else { 0 }
        Assert-True -Condition ($code -eq $expectedCode) -Because "a batch preserves the exact failed check exit code"
        $builds = @($script:fixtureCalls | Where-Object { $_[0] -ceq 'build' })
        Assert-True -Condition ($builds.Count -eq 1) -Because "a batch builds the isolated tools image once"
        $checks = @($script:fixtureCalls | Where-Object {
            $_ -contains 'ruff' -or $_ -contains 'black' -or $_ -contains 'mypy' -or $_ -contains 'pytest'
        })
        $modules = @($checks | ForEach-Object { $_[[Array]::IndexOf($_, '-m') + 1] })
        Assert-True -Condition (($modules -join '|') -ceq $batchCase.Expected) -Because "quality checks precede pytest and stop immediately on failure"
        foreach ($checkCall in $checks) {
            Assert-True -Condition ($checkCall -contains '--no-deps' -and $checkCall -contains 'tools') -Because "quality checks never implicitly start dependency services"
        }
        $downs = @($script:fixtureCalls | Where-Object { $_ -contains 'down' })
        Assert-True -Condition ($downs.Count -eq 1) -Because "every batch cleans up its isolated project once"
        $composeCall = $checks[0]
        $projectIndex = [Array]::IndexOf($composeCall, '--project-name')
        $ownedProject = $composeCall[$projectIndex + 1]
        Assert-BackendVerifyProjectName -ProjectName $ownedProject
        foreach ($checkCall in @($checks) + @($downs)) {
            Assert-True -Condition ($checkCall -contains $ownedProject) -Because "all batch checks and cleanup use the same unique project"
        }
        $serviceCalls = @($script:fixtureCalls | Where-Object {
            $_ -contains 'up' -or $_ -contains 'bootstrap' -or $_ -contains 'app.migrate' -or $_ -contains 'app.db_owner_migrate'
        })
        if ($batchCase.Check -eq 'Static' -or $batchCase.Failure) {
            Assert-True -Condition ($serviceCalls.Count -eq 0) -Because "Static and failed All quality checks never start services or bootstrap a database"
        }
        else {
            $upIndex = -1
            $lastStaticIndex = -1
            $pytestIndex = -1
            for ($index = 0; $index -lt $script:fixtureCalls.Count; $index++) {
                $call = $script:fixtureCalls[$index]
                if ($call -contains 'up') { $upIndex = $index }
                if ($call -contains 'mypy') { $lastStaticIndex = $index }
                if ($call -contains 'pytest') { $pytestIndex = $index }
            }
            Assert-True -Condition ($lastStaticIndex -lt $upIndex -and $upIndex -lt $pytestIndex) -Because "All starts database services only after static checks and before pytest"
            Assert-True -Condition (@($serviceCalls | Where-Object { $_ -contains 'bootstrap' }).Count -eq 3) -Because "All preserves the complete historical database bootstrap lifecycle"
        }
    }
}

Write-Host "Backend isolation self-test passed: $script:assertions assertions." -ForegroundColor Green
