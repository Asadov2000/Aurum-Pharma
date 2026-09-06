[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$postgresImage = "postgres:16-alpine@sha256:4327b9fd295502f326f44153a1045a7170ddbfffed1c3829798328556cfd09e2"
$redisImage = "redis:7-alpine@sha256:02f2cc4882f8bf87c79a220ac958f58c700bdec0dfb9b9ea61b62fb0e8f1bfcf"
$minioImage = "minio/minio:latest@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e"
$powershellImage = "mcr.microsoft.com/powershell:7.5-alpine-3.20@sha256:a6beeddb2fcf45547c9099fba091ce231e51aa374fe62ecc182f7c28b69a6cbf"

$suffix = [guid]::NewGuid().ToString("N").Substring(0, 12)
$prefix = "aurum-tls-test-$suffix"
$network = "$prefix-network"
$tlsVolume = "$prefix-certs"
$postgresContainer = "$prefix-postgres"
$redisContainer = "$prefix-redis"
$minioContainer = "$prefix-minio"
$createdContainers = [Collections.Generic.List[string]]::new()
$generator = (Resolve-Path (Join-Path $PSScriptRoot "New-ProductionInternalTls.ps1")).Path
$postgresEntrypoint = (
    Resolve-Path (Join-Path $PSScriptRoot "../infra/postgres/start-tls.sh")
).Path
$postgresHba = (
    Resolve-Path (Join-Path $PSScriptRoot "../infra/postgres/pg_hba.production.conf")
).Path

function Invoke-Docker {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$DiscardOutput,
        [switch]$AllowFailure
    )

    if ($DiscardOutput) {
        & docker @Arguments *> $null
    } else {
        & docker @Arguments
    }
    $exitCode = $LASTEXITCODE
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "Docker command failed with exit code $exitCode."
    }
    return $exitCode
}

function Wait-DockerCheck {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Check,
        [Parameter(Mandatory = $true)][string]$Label,
        [int]$Attempts = 30
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        if (& $Check) {
            return
        }
        Start-Sleep -Seconds 1
    }
    throw "$Label did not become ready after $Attempts attempts."
}

function Test-DockerCommand {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & docker @Arguments *> $null
    return $LASTEXITCODE -eq 0
}

try {
    [void](Invoke-Docker -Arguments @("network", "create", $network) -DiscardOutput)
    [void](Invoke-Docker -Arguments @("volume", "create", $tlsVolume) -DiscardOutput)

    [void](Invoke-Docker -Arguments @(
        "run", "--rm",
        "--volume", "${tlsVolume}:/tls",
        "--mount", "type=bind,source=$generator,target=/repo/scripts/New-ProductionInternalTls.ps1,readonly",
        "--entrypoint", "pwsh",
        $powershellImage,
        "-NoProfile", "-File", "/repo/scripts/New-ProductionInternalTls.ps1",
        "-OutputDirectory", "/tls"
    ) -DiscardOutput)

    [void](Invoke-Docker -Arguments @(
        "run", "--rm",
        "--volume", "${tlsVolume}:/tls",
        "--entrypoint", "/bin/sh",
        $powershellImage,
        "-ec", "rm -f /tls/ca.key"
    ) -DiscardOutput)

    [void](Invoke-Docker -Arguments @(
        "run", "--detach", "--name", $postgresContainer,
        "--network", $network, "--network-alias", "postgres",
        "--env", "POSTGRES_PASSWORD=tls-test-postgres-password",
        "--volume", "${tlsVolume}:/run/tls:ro",
        "--mount", "type=bind,source=$postgresEntrypoint,target=/opt/aurum/postgres/start-tls.sh,readonly",
        "--mount", "type=bind,source=$postgresHba,target=/etc/postgresql/pg_hba.production.conf,readonly",
        "--entrypoint", "/bin/sh",
        $postgresImage,
        "/opt/aurum/postgres/start-tls.sh",
        "postgres", "-c", "hba_file=/etc/postgresql/pg_hba.production.conf",
        "-c", "ssl=on",
        "-c", "ssl_cert_file=/var/lib/postgresql/tls/server.crt",
        "-c", "ssl_key_file=/var/lib/postgresql/tls/server.key",
        "-c", "ssl_ca_file=/var/lib/postgresql/tls/ca.crt",
        "-c", "ssl_min_protocol_version=TLSv1.2"
    ) -DiscardOutput)
    $createdContainers.Add($postgresContainer)

    $redisCommand = @'
set -eu
umask 077
mkdir -p /tmp/tls
cp /run/tls/ca.crt /run/tls/redis.crt /run/tls/redis.key /tmp/tls/
chown -R redis:redis /tmp/tls
chmod 600 /tmp/tls/redis.key
printf 'user default on >%s ~* +@all\n' 'tls-test-redis-password' > /tmp/users.acl
chown redis:redis /tmp/users.acl
exec gosu redis redis-server --port 0 --tls-port 6379 --tls-cert-file /tmp/tls/redis.crt --tls-key-file /tmp/tls/redis.key --tls-ca-cert-file /tmp/tls/ca.crt --tls-auth-clients no --tls-protocols 'TLSv1.2 TLSv1.3' --aclfile /tmp/users.acl --protected-mode yes
'@
    $redisCommand = $redisCommand.Replace("`r`n", "`n")
    [void](Invoke-Docker -Arguments @(
        "run", "--detach", "--name", $redisContainer,
        "--network", $network, "--network-alias", "redis",
        "--volume", "${tlsVolume}:/run/tls:ro",
        "--entrypoint", "/bin/sh",
        $redisImage,
        "-ec", $redisCommand
    ) -DiscardOutput)
    $createdContainers.Add($redisContainer)

    $minioCommand = @'
set -eu
umask 077
mkdir -p /tmp/certs/CAs
cp /run/tls/minio.crt /tmp/certs/public.crt
cp /run/tls/minio.key /tmp/certs/private.key
cp /run/tls/ca.crt /tmp/certs/CAs/aurum-internal-ca.crt
chmod 600 /tmp/certs/private.key
exec minio server --certs-dir /tmp/certs /data
'@
    $minioCommand = $minioCommand.Replace("`r`n", "`n")
    [void](Invoke-Docker -Arguments @(
        "run", "--detach", "--name", $minioContainer,
        "--network", $network, "--network-alias", "minio",
        "--env", "MINIO_ROOT_USER=tls-test-minio-user",
        "--env", "MINIO_ROOT_PASSWORD=tls-test-minio-password",
        "--volume", "${tlsVolume}:/run/tls:ro",
        "--entrypoint", "/bin/sh",
        $minioImage,
        "-ec", $minioCommand
    ) -DiscardOutput)
    $createdContainers.Add($minioContainer)

    Wait-DockerCheck -Label "PostgreSQL TLS" -Check {
        Test-DockerCommand -Arguments @(
            "exec", "--env", "PGPASSWORD=tls-test-postgres-password",
            $postgresContainer,
            "psql",
            "host=postgres dbname=postgres user=postgres sslmode=verify-full sslrootcert=/run/tls/ca.crt",
            "-Atqc", "select ssl from pg_stat_ssl where pid = pg_backend_pid();"
        )
    }
    $postgresTls = & docker exec `
        --env "PGPASSWORD=tls-test-postgres-password" `
        $postgresContainer `
        psql `
        "host=postgres dbname=postgres user=postgres sslmode=verify-full sslrootcert=/run/tls/ca.crt" `
        -Atqc "select ssl from pg_stat_ssl where pid = pg_backend_pid();"
    if ($LASTEXITCODE -ne 0 -or $postgresTls.Trim() -cne "t") {
        throw "PostgreSQL connection did not negotiate TLS."
    }

    Wait-DockerCheck -Label "Redis TLS" -Check {
        Test-DockerCommand -Arguments @(
            "exec", "--env", "REDISCLI_AUTH=tls-test-redis-password",
            $redisContainer,
            "redis-cli", "--tls", "--cacert", "/run/tls/ca.crt", "-h", "redis", "ping"
        )
    }

    Wait-DockerCheck -Label "MinIO TLS" -Check {
        Test-DockerCommand -Arguments @(
            "exec", $minioContainer,
            "curl", "--cacert", "/run/tls/ca.crt", "--fail", "--silent", "--show-error",
            "https://minio:9000/minio/health/live"
        )
    }

    $postgresPlaintext = Invoke-Docker -Arguments @(
        "exec", "--env", "PGPASSWORD=tls-test-postgres-password",
        $postgresContainer,
        "psql", "host=postgres dbname=postgres user=postgres sslmode=disable", "-Atqc", "select 1;"
    ) -AllowFailure -DiscardOutput
    if ($postgresPlaintext -eq 0) {
        throw "PostgreSQL accepted a plaintext TCP connection."
    }

    $redisPlaintext = Invoke-Docker -Arguments @(
        "exec", $redisContainer, "redis-cli", "-h", "redis", "ping"
    ) -AllowFailure -DiscardOutput
    if ($redisPlaintext -eq 0) {
        throw "Redis accepted a plaintext connection."
    }

    $minioPlaintext = Invoke-Docker -Arguments @(
        "exec", $minioContainer,
        "curl", "--fail", "--silent", "--show-error", "http://minio:9000/minio/health/live"
    ) -AllowFailure -DiscardOutput
    if ($minioPlaintext -eq 0) {
        throw "MinIO accepted a plaintext connection."
    }

    Write-Host "Production internal TLS smoke test passed."
} catch {
    foreach ($container in $createdContainers) {
        & docker logs --tail 50 $container 2>&1 | Write-Warning
    }
    throw
} finally {
    foreach ($container in $createdContainers) {
        & docker rm --force $container *> $null
    }
    & docker network rm $network *> $null
    & docker volume rm $tlsVolume *> $null
}
