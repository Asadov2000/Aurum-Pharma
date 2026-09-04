[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$validator = Join-Path $PSScriptRoot "Test-PilotReadiness.ps1"
$manifestPath = Join-Path (Split-Path $PSScriptRoot -Parent) "docs/compliance/pilot-readiness-manifest.json"
$powershellExecutable = (Get-Process -Id $PID).Path
$script:assertions = 0

function Invoke-Validator {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][ValidateSet("Validate", "Enforce")][string]$Mode
    )

    $arguments = @("-NoProfile")
    if ($env:OS -eq "Windows_NT") {
        $arguments += @("-ExecutionPolicy", "Bypass")
    }
    $arguments += @("-File", $validator, "-ManifestPath", $Path, "-Mode", $Mode, "-Format", "Json")

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $powershellExecutable @arguments *> $null
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Assert-Equal {
    param(
        [AllowNull()][object]$Actual,
        [AllowNull()][object]$Expected,
        [Parameter(Mandatory = $true)][string]$Because
    )

    $script:assertions++
    if ("$Actual" -cne "$Expected") {
        throw "Assertion failed ($Because). Expected '$Expected', got '$Actual'."
    }
}

Assert-Equal -Actual (Invoke-Validator -Path $manifestPath -Mode "Validate") -Expected 0 -Because "the committed manifest is structurally honest"
Assert-Equal -Actual (Invoke-Validator -Path $manifestPath -Mode "Enforce") -Expected 3 -Because "the current repository must not claim pilot approval"

$temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("aurum-pilot-readiness-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null
try {
    $approvedPath = Join-Path $temporaryDirectory "approved.json"
    $approved = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $approved.decision = "approved"
    $headCommit = (& git -C (Split-Path $PSScriptRoot -Parent) rev-parse HEAD | Select-Object -First 1)
    $approved.release.commit = "$headCommit"
    $approved.release.ci_evidence = "evidence://ci/test-run/v1"
    $approved.release.approved_at = "2026-01-01T00:00:00Z"
    $approved.release.valid_until = "2099-01-01T00:00:00Z"
    $approved.release.images = @(
        @{ name = "backend"; digest = "sha256:" + ("a" * 64) },
        @{ name = "gateway"; digest = "sha256:" + ("b" * 64) },
        @{ name = "recovery"; digest = "sha256:" + ("c" * 64) }
    )
    $approved.release.approvals = @(
        @{ role = "business-owner"; evidence = "evidence://approval/business/v1" },
        @{ role = "technical-lead"; evidence = "evidence://approval/technical/v1" },
        @{ role = "tajikistan-counsel"; evidence = "evidence://approval/legal/v1" },
        @{ role = "pilot-pharmacy"; evidence = "evidence://approval/pharmacy/v1" },
        @{ role = "fiscal-provider"; evidence = "evidence://approval/fiscal/v1" }
    )
    foreach ($gate in $approved.gates) {
        if ([bool]$gate.required) {
            $gate.status = "verified"
            if ($gate.verification -eq "repository") {
                $gate.evidence = @(@{ type = "repository"; ref = "AGENTS.md" })
            }
            elseif ($gate.verification -eq "external") {
                $gate.evidence = @(@{ type = "external"; ref = "evidence://test/approved/v1" })
            }
            else {
                $gate.evidence = @(
                    @{ type = "repository"; ref = "AGENTS.md" },
                    @{ type = "external"; ref = "evidence://test/approved/v1" }
                )
            }
        }
    }
    $approved | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $approvedPath -Encoding UTF8
    Assert-Equal -Actual (Invoke-Validator -Path $approvedPath -Mode "Enforce") -Expected 0 -Because "a complete approved manifest passes enforcement"

    $unsafePath = Join-Path $temporaryDirectory "unsafe.json"
    $unsafe = Get-Content -LiteralPath $approvedPath -Raw | ConvertFrom-Json
    $unsafe.gates[0].evidence = @(@{ type = "repository"; ref = "../outside.txt" })
    $unsafe | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $unsafePath -Encoding UTF8
    Assert-Equal -Actual (Invoke-Validator -Path $unsafePath -Mode "Validate") -Expected 2 -Because "evidence cannot escape the workspace"

    $forgedExternalPath = Join-Path $temporaryDirectory "forged-external.json"
    $forgedExternal = Get-Content -LiteralPath $approvedPath -Raw | ConvertFrom-Json
    $externalGate = @($forgedExternal.gates | Where-Object { $_.verification -eq "external" })[0]
    $externalGate.evidence = @(@{ type = "repository"; ref = "AGENTS.md" })
    $forgedExternal | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $forgedExternalPath -Encoding UTF8
    Assert-Equal -Actual (Invoke-Validator -Path $forgedExternalPath -Mode "Validate") -Expected 2 -Because "repository files cannot approve an external legal gate"

    $missingGatePath = Join-Path $temporaryDirectory "missing-gate.json"
    $missingGate = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $missingGate.gates = @($missingGate.gates | Select-Object -Skip 1)
    $missingGate | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $missingGatePath -Encoding UTF8
    Assert-Equal -Actual (Invoke-Validator -Path $missingGatePath -Mode "Validate") -Expected 2 -Because "a required policy gate cannot be deleted"

    $weakenedGatePath = Join-Path $temporaryDirectory "weakened-gate.json"
    $weakenedGate = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $weakenedGate.gates[0].required = $false
    $weakenedGate | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $weakenedGatePath -Encoding UTF8
    Assert-Equal -Actual (Invoke-Validator -Path $weakenedGatePath -Mode "Validate") -Expected 2 -Because "manifest cannot weaken a policy requirement"

    $disabledGatePath = Join-Path $temporaryDirectory "disabled-gate.json"
    $disabledGate = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $disabledGate.gates[0].status = "disabled"
    $disabledGate | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $disabledGatePath -Encoding UTF8
    Assert-Equal -Actual (Invoke-Validator -Path $disabledGatePath -Mode "Validate") -Expected 2 -Because "a policy gate cannot be disabled without permission"

    $scopeOverridePath = Join-Path $temporaryDirectory "scope-override.json"
    $scopeOverride = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $scopeOverride.release_scope = "unrestricted-production"
    $scopeOverride | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $scopeOverridePath -Encoding UTF8
    Assert-Equal -Actual (Invoke-Validator -Path $scopeOverridePath -Mode "Validate") -Expected 2 -Because "manifest cannot broaden the policy scope"
}
finally {
    Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
}

Write-Host "Pilot readiness tooling tests passed ($script:assertions assertions)."
