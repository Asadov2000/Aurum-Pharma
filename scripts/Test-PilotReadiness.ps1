[CmdletBinding()]
param(
    [string]$ManifestPath = "",
    [string]$PolicyPath = "",
    [ValidateSet("Validate", "Enforce")]
    [string]$Mode = "Validate",
    [ValidateSet("Text", "Json")]
    [string]$Format = "Text"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($ManifestPath)) {
    $ManifestPath = Join-Path $repoRoot "docs/compliance/pilot-readiness-manifest.json"
}
if ([string]::IsNullOrWhiteSpace($PolicyPath)) {
    $PolicyPath = Join-Path $repoRoot "docs/compliance/pilot-readiness-policy.json"
}

$errors = New-Object System.Collections.Generic.List[string]
$allowedStatuses = @("verified", "pending", "disabled", "not-applicable")
$allowedEvidenceTypes = @("repository", "external")
$allowedVerificationKinds = @("repository", "external", "hybrid")

function Add-ValidationError {
    param([Parameter(Mandatory = $true)][string]$Message)
    $errors.Add($Message)
}

function Has-Property {
    param(
        [Parameter(Mandatory = $true)][object]$Object,
        [Parameter(Mandatory = $true)][string]$Name
    )
    return $null -ne $Object.PSObject.Properties[$Name]
}

function Resolve-RepositoryEvidence {
    param([Parameter(Mandatory = $true)][string]$Reference)

    if ([System.IO.Path]::IsPathRooted($Reference)) {
        Add-ValidationError "Repository evidence must be relative: $Reference"
        return
    }

    $candidate = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $Reference))
    $rootPrefix = $repoRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $candidate.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        Add-ValidationError "Repository evidence escapes the workspace: $Reference"
        return
    }
    if (-not (Test-Path -LiteralPath $candidate)) {
        Add-ValidationError "Repository evidence does not exist: $Reference"
    }
}

try {
    $policyFile = (Resolve-Path -LiteralPath $PolicyPath).Path
    $policy = Get-Content -LiteralPath $policyFile -Raw | ConvertFrom-Json
    $manifestFile = (Resolve-Path -LiteralPath $ManifestPath).Path
    $manifest = Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json
}
catch {
    Write-Error "Cannot read pilot readiness policy or manifest: $($_.Exception.Message)"
    exit 2
}

foreach ($property in @("schema_version", "reviewed_at", "release_scope", "decision", "release", "gates")) {
    if (-not (Has-Property -Object $manifest -Name $property)) {
        Add-ValidationError "Top-level property is missing: $property"
    }
}
foreach ($property in @("schema_version", "release_scope", "gates")) {
    if (-not (Has-Property -Object $policy -Name $property)) {
        Add-ValidationError "Policy property is missing: $property"
    }
}
if ((Has-Property -Object $manifest -Name "schema_version") -and
    (Has-Property -Object $policy -Name "schema_version") -and
    "$($manifest.schema_version)" -cne "$($policy.schema_version)") {
    Add-ValidationError "Manifest schema_version must match the policy."
}
if ((Has-Property -Object $manifest -Name "release_scope") -and
    (Has-Property -Object $policy -Name "release_scope") -and
    "$($manifest.release_scope)" -cne "$($policy.release_scope)") {
    Add-ValidationError "Manifest release_scope must match the policy."
}

$policyGates = @()
if (Has-Property -Object $policy -Name "gates") {
    $policyGates = @($policy.gates)
}
if ($policyGates.Count -eq 0) {
    Add-ValidationError "The policy must contain at least one gate."
}
$policyById = @{}
foreach ($policyGate in $policyGates) {
    foreach ($property in @("id", "category", "required", "verification", "allow_disabled")) {
        if (-not (Has-Property -Object $policyGate -Name $property)) {
            Add-ValidationError "A policy gate is missing property '$property'."
        }
    }
    if (-not (Has-Property -Object $policyGate -Name "id")) {
        continue
    }
    $policyId = "$($policyGate.id)"
    if ($policyById.ContainsKey($policyId)) {
        Add-ValidationError "Duplicate policy gate id: $policyId"
    }
    else {
        $policyById[$policyId] = $policyGate
    }
}

$gates = @()
if (Has-Property -Object $manifest -Name "gates") {
    $gates = @($manifest.gates)
}
if ($gates.Count -eq 0) {
    Add-ValidationError "The manifest must contain at least one gate."
}

$seenIds = @{}
$blockingIds = New-Object System.Collections.Generic.List[string]
$verifiedCount = 0
$disabledCount = 0

foreach ($gate in $gates) {
    foreach ($property in @("id", "category", "title", "owner", "required", "verification", "status", "evidence")) {
        if (-not (Has-Property -Object $gate -Name $property)) {
            Add-ValidationError "A gate is missing property '$property'."
        }
    }
    if (-not (Has-Property -Object $gate -Name "id")) {
        continue
    }

    $id = "$($gate.id)"
    if ([string]::IsNullOrWhiteSpace($id)) {
        Add-ValidationError "A gate has an empty id."
        continue
    }
    if ($seenIds.ContainsKey($id)) {
        Add-ValidationError "Duplicate gate id: $id"
    }
    else {
        $seenIds[$id] = $true
    }

    if (-not $policyById.ContainsKey($id)) {
        Add-ValidationError "Gate $id is not declared by the pilot readiness policy."
    }
    else {
        $policyGate = $policyById[$id]
        foreach ($property in @("category", "required", "verification")) {
            if ((Has-Property -Object $gate -Name $property) -and
                "$($gate.$property)" -cne "$($policyGate.$property)") {
                Add-ValidationError "Gate $id cannot override policy property '$property'."
            }
        }
    }

    $status = if (Has-Property -Object $gate -Name "status") { "$($gate.status)" } else { "" }
    if ($allowedStatuses -cnotcontains $status) {
        Add-ValidationError "Gate $id has unsupported status '$status'."
    }
    if ($status -eq "disabled" -and $policyById.ContainsKey($id) -and -not [bool]$policyById[$id].allow_disabled) {
        Add-ValidationError "Gate $id cannot be disabled by policy."
    }

    if ((Has-Property -Object $gate -Name "owner") -and [string]::IsNullOrWhiteSpace("$($gate.owner)")) {
        Add-ValidationError "Gate $id must have an owner."
    }

    $verification = if (Has-Property -Object $gate -Name "verification") { "$($gate.verification)" } else { "" }
    if ($allowedVerificationKinds -cnotcontains $verification) {
        Add-ValidationError "Gate $id has unsupported verification kind '$verification'."
    }

    $required = (Has-Property -Object $gate -Name "required") -and [bool]$gate.required
    $evidence = @()
    if (Has-Property -Object $gate -Name "evidence") {
        $evidence = @($gate.evidence)
    }
    if (($status -eq "verified" -or $status -eq "disabled") -and $evidence.Count -eq 0) {
        Add-ValidationError "Gate $id with status '$status' must have evidence."
    }

    $hasRepositoryEvidence = $false
    $hasExternalEvidence = $false
    foreach ($item in $evidence) {
        if (-not (Has-Property -Object $item -Name "type") -or -not (Has-Property -Object $item -Name "ref")) {
            Add-ValidationError "Gate $id has malformed evidence."
            continue
        }
        $evidenceType = "$($item.type)"
        $reference = "$($item.ref)"
        if ($allowedEvidenceTypes -cnotcontains $evidenceType) {
            Add-ValidationError "Gate $id has unsupported evidence type '$evidenceType'."
            continue
        }
        if ([string]::IsNullOrWhiteSpace($reference) -or $reference -match "(?i)(TODO|TBD|PLACEHOLDER|ЗАПОЛНИТЬ)") {
            Add-ValidationError "Gate $id has empty or placeholder evidence."
            continue
        }
        if ($evidenceType -eq "repository") {
            $hasRepositoryEvidence = $true
            Resolve-RepositoryEvidence -Reference $reference
        }
        else {
            $hasExternalEvidence = $true
            if (-not $reference.StartsWith("evidence://", [System.StringComparison]::OrdinalIgnoreCase)) {
                Add-ValidationError "External evidence for gate $id must use an evidence:// identifier, not a secret or URL."
            }
        }
    }

    if ($status -eq "verified") {
        if (($verification -eq "repository" -or $verification -eq "hybrid") -and -not $hasRepositoryEvidence) {
            Add-ValidationError "Verified gate $id requires repository evidence."
        }
        if (($verification -eq "external" -or $verification -eq "hybrid") -and -not $hasExternalEvidence) {
            Add-ValidationError "Verified gate $id requires external evidence."
        }
    }
    if ($status -eq "disabled" -and -not $hasRepositoryEvidence) {
        Add-ValidationError "Disabled gate $id requires repository evidence that the feature is off."
    }

    if ($status -eq "verified") {
        $verifiedCount++
    }
    elseif ($status -eq "disabled") {
        $disabledCount++
    }

    if ($required -and $status -ne "verified" -and $status -ne "disabled") {
        $blockingIds.Add($id)
    }
}

foreach ($policyId in $policyById.Keys) {
    if (-not $seenIds.ContainsKey($policyId)) {
        Add-ValidationError "Required policy gate is missing from the manifest: $policyId"
    }
}

$decision = if (Has-Property -Object $manifest -Name "decision") { "$($manifest.decision)" } else { "" }
if ($decision -cnotin @("blocked", "approved")) {
    Add-ValidationError "Decision must be 'blocked' or 'approved'."
}
if ($decision -eq "approved" -and $blockingIds.Count -gt 0) {
    Add-ValidationError "Decision cannot be approved while required gates are open."
}
if ((Has-Property -Object $manifest -Name "release") -and $decision -eq "approved") {
    $release = $manifest.release
    foreach ($property in @("commit", "ci_evidence", "approved_at", "valid_until", "images", "approvals")) {
        if (-not (Has-Property -Object $release -Name $property)) {
            Add-ValidationError "Approved release is missing property '$property'."
        }
    }

    $releaseCommit = if (Has-Property -Object $release -Name "commit") { "$($release.commit)" } else { "" }
    if ($releaseCommit -notmatch "^[0-9a-f]{40}$") {
        Add-ValidationError "Approved release must contain a full 40-character Git commit."
    }
    else {
        $headCommit = (& git -C $repoRoot rev-parse HEAD 2>$null | Select-Object -First 1)
        if ([string]::IsNullOrWhiteSpace("$headCommit") -or "$headCommit" -cne $releaseCommit) {
            Add-ValidationError "Approved release commit must match the checked-out HEAD."
        }
    }

    $ciEvidence = if (Has-Property -Object $release -Name "ci_evidence") { "$($release.ci_evidence)" } else { "" }
    if (-not $ciEvidence.StartsWith("evidence://", [System.StringComparison]::OrdinalIgnoreCase)) {
        Add-ValidationError "Approved release requires an evidence:// CI run identifier."
    }

    $approvedAt = [datetimeoffset]::MinValue
    $validUntil = [datetimeoffset]::MinValue
    if (-not [datetimeoffset]::TryParse("$($release.approved_at)", [ref]$approvedAt)) {
        Add-ValidationError "Approved release has an invalid approved_at timestamp."
    }
    if (-not [datetimeoffset]::TryParse("$($release.valid_until)", [ref]$validUntil)) {
        Add-ValidationError "Approved release has an invalid valid_until timestamp."
    }
    elseif ($validUntil -le [datetimeoffset]::UtcNow) {
        Add-ValidationError "Approved release has expired."
    }
    if ($approvedAt -ne [datetimeoffset]::MinValue -and $validUntil -ne [datetimeoffset]::MinValue -and $approvedAt -ge $validUntil) {
        Add-ValidationError "approved_at must be earlier than valid_until."
    }

    $requiredImages = @("backend", "gateway", "recovery")
    $images = @()
    if (Has-Property -Object $release -Name "images") {
        $images = @($release.images)
    }
    foreach ($imageName in $requiredImages) {
        $matchingImages = @($images | Where-Object { "$($_.name)" -ceq $imageName })
        if ($matchingImages.Count -ne 1 -or "$($matchingImages[0].digest)" -notmatch "^sha256:[0-9a-f]{64}$") {
            Add-ValidationError "Approved release requires one valid digest for image '$imageName'."
        }
    }

    $requiredApprovals = @("business-owner", "technical-lead", "tajikistan-counsel", "pilot-pharmacy", "fiscal-provider")
    $approvals = @()
    if (Has-Property -Object $release -Name "approvals") {
        $approvals = @($release.approvals)
    }
    foreach ($approvalRole in $requiredApprovals) {
        $matchingApprovals = @($approvals | Where-Object { "$($_.role)" -ceq $approvalRole })
        if ($matchingApprovals.Count -ne 1 -or -not "$($matchingApprovals[0].evidence)".StartsWith("evidence://", [System.StringComparison]::OrdinalIgnoreCase)) {
            Add-ValidationError "Approved release requires one evidence:// approval for '$approvalRole'."
        }
    }
}

$result = [ordered]@{
    manifest = $ManifestPath
    policy = $PolicyPath
    mode = $Mode
    decision = $decision
    total = $gates.Count
    verified = $verifiedCount
    disabled = $disabledCount
    blocking = $blockingIds.Count
    blocking_ids = @($blockingIds)
    validation_errors = @($errors)
}

if ($Format -eq "Json") {
    $result | ConvertTo-Json -Depth 6
}
else {
    Write-Host "Pilot readiness: decision=$decision, verified=$verifiedCount, disabled=$disabledCount, blocking=$($blockingIds.Count)."
    if ($blockingIds.Count -gt 0) {
        Write-Host "Open gates: $($blockingIds -join ', ')" -ForegroundColor Yellow
    }
    foreach ($validationError in $errors) {
        Write-Host "ERROR: $validationError" -ForegroundColor Red
    }
}

if ($errors.Count -gt 0) {
    exit 2
}
if ($Mode -eq "Enforce" -and ($decision -ne "approved" -or $blockingIds.Count -gt 0)) {
    exit 3
}
exit 0
