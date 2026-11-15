param([Alias('Profile')][ValidateSet('cloud','local','slurm')][string]$ExecutionProfile = 'cloud')
$ErrorActionPreference = 'Stop'
$experimentRoot = Split-Path $PSScriptRoot -Parent
Push-Location $experimentRoot
try {
    & "$experimentRoot/.venv/Scripts/expops.exe" run "configs/compute.$ExecutionProfile.yaml"
    if ($LASTEXITCODE -ne 0) { throw "ExpOps exited with code $LASTEXITCODE" }
} finally { Pop-Location }
