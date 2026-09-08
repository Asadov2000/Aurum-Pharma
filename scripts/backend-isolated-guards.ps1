# Pure guards: safe to dot-source without invoking Docker or writing files.
function Assert-BackendVerifyProjectName {
    param([AllowEmptyString()][string]$ProjectName)
    if ($ProjectName -cnotmatch '^aurum-verify-[0-9a-f]{32}$') {
        throw 'Refusing a non-disposable backend verification project.'
    }
}

function Assert-BackendVerifyResourceOwnership {
    param(
        [string]$ProjectName,
        [AllowEmptyString()][string]$ActualProject,
        [AllowEmptyString()][string]$ResourceName
    )
    Assert-BackendVerifyProjectName $ProjectName
    $name = $ResourceName.TrimStart('/')
    if ($ActualProject -cne $ProjectName -or
        ($name -cnotlike "$ProjectName-*" -and $name -cnotlike "${ProjectName}_*")) {
        throw 'Refusing cleanup: resource ownership does not match this invocation.'
    }
}
