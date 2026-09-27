[CmdletBinding()]
param(
    [string]$BackendImage = "aurum-pharma-e2e-backend:local",
    [switch]$VerifyMigration0151Downgrade,
    [switch]$VerifyRestore,
    [string]$BackupImage = "aurum-pharma-backup:ci"
)

# Disposable read-only Edge smoke. Unique labels and exact ownership checks
# guard cleanup; no published port, named volume, shared Compose project or
# access to the host's development/demo database.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backend = Join-Path $workspace "backend"
$infra = Join-Path $workspace "infra"
$scratch = Join-Path $workspace ".codex"
[void](New-Item -ItemType Directory -Path $scratch -Force)
$prefix = "aurum-edge-smoke-$([guid]::NewGuid().ToString('N').Substring(0,12))"
$network = "$prefix-network"
$restoreNetwork = "$prefix-restore-network"
$label = "tj.aurum.edge.smoke=$prefix"
$containers = [Collections.Generic.List[string]]::new()
$temporaryFiles = [Collections.Generic.List[string]]::new()
$networkCreated = $false
$restoreNetworkCreated = $false
$postgresImage = "postgres:16-alpine@sha256:4327b9fd295502f326f44153a1045a7170ddbfffed1c3829798328556cfd09e2"
$sourceMounts = @()
foreach ($source in @("app", "scripts", "alembic", "alembic.ini")) {
    $sourceMounts += @("--mount", "type=bind,source=$backend/$source,target=/app/$source,readonly")
}

function New-SmokeSecret {
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) }
    finally { $generator.Dispose() }
    return [BitConverter]::ToString($bytes).Replace("-", "").ToLowerInvariant()
}

function Write-SmokeEnvironment {
    param([string]$Name, [hashtable]$Values)
    $path = Join-Path $scratch "$prefix-$Name.env"
    $temporaryFiles.Add($path)
    [IO.File]::WriteAllLines(
        $path,
        [string[]]@($Values.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }),
        [Text.UTF8Encoding]::new($false)
    )
    return $path
}

function Invoke-SmokeDocker {
    param([string[]]$Arguments, [switch]$Quiet)
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        if ($Quiet) { & docker @Arguments *> $null }
        else { & docker @Arguments }
        $code = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    if ($code -ne 0) { throw "Isolated Edge smoke Docker step failed (exit $code)." }
}

function Test-OwnedResource {
    param([string]$Kind, [string]$Name)
    if (-not $Name.StartsWith("$prefix-", [StringComparison]::Ordinal)) {
        throw "Refusing cleanup of a resource outside the invocation prefix."
    }
    $format = if ($Kind -eq "container") { '{{json .Config.Labels}}' }
    else { '{{json .Labels}}' }
    $rawLabels = (& docker $Kind inspect --format $format $Name).Trim()
    $actual = ($rawLabels | ConvertFrom-Json).'tj.aurum.edge.smoke'
    if ($LASTEXITCODE -ne 0 -or $actual -cne $prefix) {
        throw "Refusing cleanup of an unowned isolated resource."
    }
}

function Invoke-Driver {
    param([string]$Phase, [int]$ExpectedExitCode = 0, [string]$NetworkName = $network)
    $arguments = @("run", "--rm", "--network", $NetworkName, "--env-file", $controllerEnv, "--label", $label)
    $arguments += $sourceMounts
    $arguments += @("--workdir", "/app", "--entrypoint", "python", $BackendImage, "scripts/smoke_edge_cold_read.py", $Phase)
    if ($ExpectedExitCode -eq 0) {
        Invoke-SmokeDocker -Arguments $arguments
        return
    }
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & docker @arguments *> $null
        $code = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    if ($code -ne $ExpectedExitCode) {
        throw "Isolated Edge interrupted install returned unexpected exit $code."
    }
    Write-Host "interrupted_snapshot_install=process_exited"
}

function Wait-Health {
    param([int]$Status, [string]$Edge)
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & docker exec $apiName python /app/scripts/smoke_edge_cold_read.py health --expected $Status --expected-edge $Edge *> $null
            $code = $LASTEXITCODE
        }
        finally { $ErrorActionPreference = $previousPreference }
        if ($code -eq 0) {
            Write-Host "health=$Status edge_snapshot=$Edge"
            return
        }
        Start-Sleep -Seconds 1
    }
    throw "Isolated Edge API did not reach expected health $Status/$Edge."
}

function Wait-Postgres {
    param([string]$ContainerName)
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & docker exec $ContainerName pg_isready -U postgres -d $database.POSTGRES_DB *> $null
            $code = $LASTEXITCODE
        }
        finally { $ErrorActionPreference = $previousPreference }
        if ($code -eq 0) { return }
        Start-Sleep -Seconds 1
    }
    throw "Isolated Edge PostgreSQL did not become ready."
}

function New-RecoveryContainer {
    param([string]$Name, [string]$NetworkName, [string]$KeyPath)
    $containers.Add($Name)
    $arguments = @(
        "run", "--detach", "--name", $Name, "--label", $label,
        "--network", $NetworkName, "--env-file", $databaseEnv,
        "--env", "RESTIC_PASSWORD_FILE=/run/recovery-key",
        "--mount", "type=bind,source=$KeyPath,target=/run/recovery-key,readonly",
        "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
        "--mount", "type=bind,source=$workspace/scripts/edge-read-recovery-drill.sh,target=/drill.sh,readonly",
        "--memory=1g", "--cpus=1", "--entrypoint", "sleep"
    )
    foreach ($path in @("workspace", "payload", "repository", "transfer", "corrupt", "restored")) {
        $arguments += @("--tmpfs", "/${path}:rw,noexec,nosuid,nodev,size=128m,uid=10001,gid=10001,mode=0700")
    }
    $arguments += @($BackupImage, "infinity")
    Invoke-SmokeDocker -Arguments $arguments -Quiet
}

function Copy-RecoveryArchive {
    param([string]$Name, [string]$Path, [switch]$ToContainer)
    Test-OwnedResource -Kind "container" -Name $Name
    # The invocation prefix is generated locally; prohibit shell metacharacters.
    if ($Name -cnotmatch '^aurum-edge-smoke-[a-f0-9]{12}-(backup|restore-backup)$') {
        throw "Refusing binary transfer for an unexpected recovery container."
    }
    $expectedPath = [IO.Path]::GetFullPath((Join-Path $scratch "$prefix-read-backup.tar"))
    if ([IO.Path]::GetFullPath($Path) -cne $expectedPath) {
        throw "Refusing binary transfer outside the invocation archive path."
    }
    # Docker Desktop cannot docker-cp tmpfs files. Stream native bytes through
    # exec instead of PowerShell's text pipeline (which corrupts binary on 5.1).
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = "docker"
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.RedirectStandardInput = [bool]$ToContainer
    $start.Arguments = if ($ToContainer) {
        "exec -i $Name sh -c `"umask 077; cat > /transfer/repository.tar`""
    } else { "exec $Name cat /transfer/repository.tar" }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    $stream = $null
    $started = $false
    try {
        $mode = if ($ToContainer) { [IO.FileMode]::Open } else { [IO.FileMode]::CreateNew }
        $access = if ($ToContainer) { [IO.FileAccess]::Read } else { [IO.FileAccess]::Write }
        $stream = [IO.File]::Open($Path, $mode, $access, [IO.FileShare]::None)
        $started = $process.Start()
        if (-not $started) { throw "Could not start isolated binary transfer." }
        $stderr = $process.StandardError.ReadToEndAsync()
        if ($ToContainer) {
            $stdout = $process.StandardOutput.ReadToEndAsync()
            $stream.CopyTo($process.StandardInput.BaseStream)
            $process.StandardInput.Close()
        } else {
            $process.StandardOutput.BaseStream.CopyTo($stream)
        }
        $process.WaitForExit()
        [void]$stderr.GetAwaiter().GetResult()
        if ($ToContainer) { [void]$stdout.GetAwaiter().GetResult() }
        if ($process.ExitCode -ne 0) { throw "Isolated binary recovery transfer failed." }
    }
    finally {
        if ($stream) { $stream.Dispose() }
        if ($started -and -not $process.HasExited) { $process.Kill() }
        $process.Dispose()
    }
}

try {
    Invoke-SmokeDocker -Arguments @("image", "inspect", $BackendImage) -Quiet
    if ($VerifyRestore) {
        Invoke-SmokeDocker -Arguments @("image", "inspect", $BackupImage) -Quiet
    }
    $database = @{
        POSTGRES_HOST = "edge-benchmark-postgres"
        POSTGRES_DB = "aurum_edge_read_benchmark_test"
        POSTGRES_USER = "postgres"
        POSTGRES_PASSWORD = New-SmokeSecret
    }
    foreach ($role in @("APP", "SUPPORT", "MAILER", "BILLING_WORKER", "WORKER", "CATALOG_WORKER", "MIGRATOR", "BACKUP", "PITR")) {
        $database["AURUM_${role}_PASSWORD"] = New-SmokeSecret
    }
    $databaseEnv = Write-SmokeEnvironment "database" $database
    $controller = @{
        ENVIRONMENT = "development"
        DEPLOYMENT_PROFILE = "edge_shadow"
        PYTHONDONTWRITEBYTECODE = "1"
        PYTHONUNBUFFERED = "1"
        DATABASE_URL_APP = "postgresql+asyncpg://aurum_app:$($database.AURUM_APP_PASSWORD)@edge-benchmark-postgres:5432/aurum_edge_read_benchmark_test"
        DATABASE_URL_SUPPORT = "postgresql+asyncpg://aurum_support:$($database.AURUM_SUPPORT_PASSWORD)@edge-benchmark-postgres:5432/aurum_edge_read_benchmark_test"
        DATABASE_URL_MIGRATION = "postgresql+asyncpg://aurum_migrator:$($database.AURUM_MIGRATOR_PASSWORD)@edge-benchmark-postgres:5432/aurum_edge_read_benchmark_test"
        REDIS_URL = "redis://edge-smoke-redis:6379/0"
        JWT_SECRET = New-SmokeSecret
        EDGE_SYNC_CREDENTIAL = "edge_v1.$([guid]::NewGuid()).$(New-SmokeSecret)"
    }
    $controllerEnv = Write-SmokeEnvironment "controller" $controller
    Invoke-SmokeDocker -Arguments @("network", "create", "--internal", "--label", $label, $network) -Quiet
    $networkCreated = $true
    $isInternal = (& docker network inspect --format '{{.Internal}}' $network).Trim()
    if ($LASTEXITCODE -ne 0 -or $isInternal -cne "true") {
        throw "Isolated Edge smoke network is not internal."
    }

    $postgresName = "$prefix-postgres"
    $containers.Add($postgresName)
    Invoke-SmokeDocker -Arguments @(
        "run", "--detach", "--name", $postgresName, "--label", $label,
        "--network", $network, "--network-alias", "edge-benchmark-postgres",
        "--env-file", $databaseEnv,
        "--mount", "type=bind,source=$infra/postgres/init.sh,target=/docker-entrypoint-initdb.d/init.sh,readonly",
        "--mount", "type=bind,source=$infra/postgres/role-contract.sql,target=/docker-entrypoint-initdb.d/role-contract.inc,readonly",
        "--memory=1g", "--cpus=1", $postgresImage
    ) -Quiet
    Wait-Postgres -ContainerName $postgresName

    $redisName = "$prefix-redis"
    $containers.Add($redisName)
    Invoke-SmokeDocker -Arguments @(
        "run", "--detach", "--name", $redisName, "--label", $label,
        "--network", $network, "--network-alias", "edge-smoke-redis",
        "--memory=128m", "redis:7-alpine", "redis-server", "--save", "", "--appendonly", "no"
    ) -Quiet
    Invoke-SmokeDocker -Arguments @(
        "run", "--rm", "--network", $network, "--label", $label, "--env-file", $databaseEnv,
        "--mount", "type=bind,source=$infra/postgres,target=/bootstrap,readonly",
        "--entrypoint", "/bin/sh", $postgresImage, "/bootstrap/bootstrap-roles.sh"
    ) -Quiet
    $arguments = @("run", "--rm", "--network", $network, "--label", $label, "--env-file", $controllerEnv)
    $arguments += $sourceMounts
    $arguments += @("--workdir", "/app", "--entrypoint", "python", $BackendImage, "-m", "app.migrate", "legacy-upgrade", "0029")
    Invoke-SmokeDocker -Arguments $arguments -Quiet
    Invoke-SmokeDocker -Arguments @(
        "run", "--rm", "--network", $network, "--label", $label, "--env-file", $databaseEnv,
        "--mount", "type=bind,source=$infra/postgres,target=/bootstrap,readonly",
        "--entrypoint", "/bin/sh", $postgresImage, "/bootstrap/bootstrap-roles.sh"
    ) -Quiet
    $arguments = @(
        "run", "--rm", "--network", $network, "--label", $label, "--env-file", $databaseEnv,
        "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "MIGRATION_DB_HOST=edge-benchmark-postgres",
        "--env", "MIGRATION_DB_NAME=aurum_edge_read_benchmark_test"
    )
    $arguments += $sourceMounts
    $arguments += @("--workdir", "/app", "--entrypoint", "python", $BackendImage, "-m", "app.db_owner_migrate")
    Invoke-SmokeDocker -Arguments $arguments -Quiet
    Invoke-SmokeDocker -Arguments @(
        "run", "--rm", "--network", $network, "--label", $label, "--env-file", $databaseEnv,
        "--mount", "type=bind,source=$infra/postgres,target=/bootstrap,readonly",
        "--entrypoint", "/bin/sh", $postgresImage, "/bootstrap/bootstrap-roles.sh"
    ) -Quiet
    $arguments = @("run", "--rm", "--network", $network, "--label", $label, "--env-file", $controllerEnv)
    $arguments += $sourceMounts
    $arguments += @("--workdir", "/app", "--entrypoint", "python", $BackendImage, "-m", "app.migrate", "upgrade", "head")
    Invoke-SmokeDocker -Arguments $arguments -Quiet
    if ($VerifyMigration0151Downgrade) {
        $downgradeArguments = @("run", "--rm", "--network", $network, "--label", $label, "--env-file", $controllerEnv)
        $downgradeArguments += $sourceMounts
        $downgradeArguments += @(
            "--workdir", "/app", "--entrypoint", "python", $BackendImage,
            "-m", "app.migrate", "downgrade", "0150", "--allow-test-downgrade"
        )
        Invoke-SmokeDocker -Arguments $downgradeArguments -Quiet
        Invoke-SmokeDocker -Arguments $arguments -Quiet
        Write-Host "migration_0151_downgrade_upgrade=ok"
    }

    $apiName = "$prefix-api"
    $containers.Add($apiName)
    $arguments = @("run", "--detach", "--name", $apiName, "--label", $label, "--network", $network, "--env-file", $controllerEnv)
    $arguments += $sourceMounts
    $arguments += @("--workdir", "/app", "--memory=1g", "--cpus=1", $BackendImage, "python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000")
    Invoke-SmokeDocker -Arguments $arguments -Quiet
    Wait-Health -Status 503 -Edge "false"
    Invoke-Driver -Phase "seed"
    Wait-Health -Status 200 -Edge "true"
    Invoke-Driver -Phase "read"

    Invoke-Driver -Phase "interrupt" -ExpectedExitCode 77
    Wait-Health -Status 200 -Edge "true"
    Invoke-Driver -Phase "read"
    Invoke-Driver -Phase "recover"
    Wait-Health -Status 200 -Edge "true"
    Invoke-Driver -Phase "read_recovered"

    Invoke-SmokeDocker -Arguments @("stop", $postgresName) -Quiet
    Wait-Health -Status 503 -Edge "any"
    Invoke-SmokeDocker -Arguments @("start", $postgresName) -Quiet
    Wait-Health -Status 200 -Edge "true"
    Invoke-SmokeDocker -Arguments @("restart", $apiName) -Quiet
    Wait-Health -Status 200 -Edge "true"
    Invoke-Driver -Phase "read_recovered"
    if ($VerifyRestore) {
        # Reuse the shipped restic tool without exposing plaintext on the host.
        # Synthetic keys travel separately from the encrypted repository archive.
        $backupPath = Join-Path $scratch "$prefix-read-backup.tar"
        $temporaryFiles.Add($backupPath)
        $recoveryKeyPath = Join-Path $scratch "$prefix-recovery.key"
        $temporaryFiles.Add($recoveryKeyPath)
        [IO.File]::WriteAllText($recoveryKeyPath, (New-SmokeSecret), [Text.UTF8Encoding]::new($false))
        $backupName = "$prefix-backup"
        Test-OwnedResource -Kind "container" -Name $postgresName
        New-RecoveryContainer -Name $backupName -NetworkName $network -KeyPath $recoveryKeyPath
        Invoke-SmokeDocker -Arguments @("exec", $backupName, "sh", "/drill.sh", "backup")
        $snapshotId = (& docker exec $backupName cat /transfer/snapshot-id).Trim()
        if ($LASTEXITCODE -ne 0 -or $snapshotId -cnotmatch '^[0-9a-f]{64}$') {
            throw "Could not obtain the completed isolated Edge backup snapshot."
        }
        $sourceHashLine = (& docker exec $backupName sha256sum /transfer/repository.tar).Trim()
        if ($LASTEXITCODE -ne 0 -or $sourceHashLine -notmatch '^([0-9a-f]{64})\s') {
            throw "Could not verify the isolated Edge archive checksum."
        }
        $sourceHash = $Matches[1]
        Copy-RecoveryArchive -Name $backupName -Path $backupPath
        $copiedHash = (Get-FileHash -LiteralPath $backupPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($copiedHash -cne $sourceHash) {
            throw "Isolated Edge archive checksum changed during copy."
        }
        Invoke-SmokeDocker -Arguments @("stop", $apiName, $redisName, $postgresName, $backupName) -Quiet
        $restoreTimer = [Diagnostics.Stopwatch]::StartNew()

        Invoke-SmokeDocker -Arguments @(
            "network", "create", "--internal", "--label", $label, $restoreNetwork
        ) -Quiet
        $restoreNetworkCreated = $true
        $restoreInternal = (& docker network inspect --format '{{.Internal}}' $restoreNetwork).Trim()
        if ($LASTEXITCODE -ne 0 -or $restoreInternal -cne "true") {
            throw "Isolated Edge restore network is not internal."
        }
        $restorePostgresName = "$prefix-restore-postgres"
        $containers.Add($restorePostgresName)
        Invoke-SmokeDocker -Arguments @(
            "run", "--detach", "--name", $restorePostgresName, "--label", $label,
            "--network", $restoreNetwork, "--network-alias", "edge-benchmark-postgres",
            "--env-file", $databaseEnv,
            "--mount", "type=bind,source=$infra/postgres/init.sh,target=/docker-entrypoint-initdb.d/init.sh,readonly",
            "--mount", "type=bind,source=$infra/postgres/role-contract.sql,target=/docker-entrypoint-initdb.d/role-contract.inc,readonly",
            "--memory=1g", "--cpus=1", $postgresImage
        ) -Quiet
        Wait-Postgres -ContainerName $restorePostgresName
        $restoreBackupName = "$prefix-restore-backup"
        New-RecoveryContainer -Name $restoreBackupName -NetworkName $restoreNetwork -KeyPath $recoveryKeyPath
        Copy-RecoveryArchive -Name $restoreBackupName -Path $backupPath -ToContainer
        $restoredHashLine = (& docker exec $restoreBackupName sha256sum /transfer/repository.tar).Trim()
        if ($LASTEXITCODE -ne 0 -or $restoredHashLine -notmatch '^([0-9a-f]{64})\s' -or
            $Matches[1] -cne $sourceHash) {
            throw "Isolated Edge archive checksum changed before restore."
        }
        Test-OwnedResource -Kind "container" -Name $restorePostgresName
        Invoke-SmokeDocker -Arguments @("exec", $restoreBackupName, "sh", "/drill.sh", "restore", $snapshotId)

        $restoreRedisName = "$prefix-restore-redis"
        $containers.Add($restoreRedisName)
        Invoke-SmokeDocker -Arguments @(
            "run", "--detach", "--name", $restoreRedisName, "--label", $label,
            "--network", $restoreNetwork, "--network-alias", "edge-smoke-redis",
            "--memory=128m", "redis:7-alpine", "redis-server", "--save", "", "--appendonly", "no"
        ) -Quiet
        $apiName = "$prefix-restore-api"
        $containers.Add($apiName)
        $arguments = @(
            "run", "--detach", "--name", $apiName, "--label", $label,
            "--network", $restoreNetwork, "--env-file", $controllerEnv
        )
        $arguments += $sourceMounts
        $arguments += @(
            "--workdir", "/app", "--memory=1g", "--cpus=1", $BackendImage,
            "python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"
        )
        Invoke-SmokeDocker -Arguments $arguments -Quiet
        Wait-Health -Status 200 -Edge "true"
        Invoke-Driver -Phase "read_recovered" -NetworkName $restoreNetwork
        $restoreTimer.Stop()
        Invoke-SmokeDocker -Arguments @("restart", $apiName) -Quiet
        Wait-Health -Status 200 -Edge "true"
        Invoke-Driver -Phase "read_recovered" -NetworkName $restoreNetwork
        Write-Host "isolated_edge_encrypted_read_restore=verified recovery_seconds=$([Math]::Round($restoreTimer.Elapsed.TotalSeconds, 1))"
    }
    Write-Host "[OK] Isolated Edge read-only cold/interrupted-install/restart smoke passed."
}
finally {
    $cleanupFailed = $false
    for ($index = $containers.Count - 1; $index -ge 0; $index--) {
        $name = $containers[$index]
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & docker container inspect $name *> $null
            $code = $LASTEXITCODE
        }
        finally { $ErrorActionPreference = $previousPreference }
        if ($code -eq 0) {
            try {
                Test-OwnedResource -Kind "container" -Name $name
                Invoke-SmokeDocker -Arguments @("rm", "--force", "--volumes", $name) -Quiet
            }
            catch { $cleanupFailed = $true }
        }
    }
    if ($restoreNetworkCreated) {
        try {
            Test-OwnedResource -Kind "network" -Name $restoreNetwork
            Invoke-SmokeDocker -Arguments @("network", "rm", $restoreNetwork) -Quiet
        }
        catch { $cleanupFailed = $true }
    }
    if ($networkCreated) {
        try {
            Test-OwnedResource -Kind "network" -Name $network
            Invoke-SmokeDocker -Arguments @("network", "rm", $network) -Quiet
        }
        catch { $cleanupFailed = $true }
    }
    foreach ($path in $temporaryFiles) {
        $resolved = [IO.Path]::GetFullPath($path)
        if (-not $resolved.StartsWith("$scratch$([IO.Path]::DirectorySeparatorChar)$prefix-", [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing cleanup of a file outside the isolated scratch prefix."
        }
        try {
            # A preceding step may fail before creating a registered output file.
            if (Test-Path -LiteralPath $resolved) {
                Remove-Item -LiteralPath $resolved -Force -ErrorAction Stop
            }
        }
        catch { $cleanupFailed = $true }
    }
    if ($cleanupFailed) {
        throw "Isolated Edge smoke cleanup failed; inspect resources labelled $label."
    }
}
