param(
    [Parameter(Mandatory = $true)]
    [string]$OldProject,

    [string]$NewProject
)

if ([string]::IsNullOrWhiteSpace($NewProject)) {
    $NewProject = Join-Path $PSScriptRoot ".."
}

$ErrorActionPreference = "Stop"
$OldProject = (Resolve-Path $OldProject).Path
$NewProject = (Resolve-Path $NewProject).Path
$runtime = Join-Path $NewProject "runtime"
New-Item -ItemType Directory -Force -Path $runtime | Out-Null

foreach ($name in @("data", "models", "logs", "recordings")) {
    $old = Join-Path $OldProject $name
    $new = Join-Path $runtime $name
    if (-not (Test-Path $old)) {
        continue
    }
    if (Test-Path $new) {
        Write-Host "Skipping $name because runtime\$name already exists."
        continue
    }
    Copy-Item -Recurse -Path $old -Destination $new
    Write-Host "Copied $name -> runtime\$name"
}

$oldEnv = Join-Path $OldProject ".env"
$newEnv = Join-Path $NewProject ".env"
if ((Test-Path $oldEnv) -and -not (Test-Path $newEnv)) {
    Copy-Item $oldEnv $newEnv
    Write-Host "Copied .env"
}

Write-Host "Import complete. Your old Ron folder was not modified."
