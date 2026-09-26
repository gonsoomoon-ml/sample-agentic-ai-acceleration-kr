<#
.SYNOPSIS
  Build policy-helper.exe (PyInstaller onedir) and put the site baseline next to it.

.DESCRIPTION
  Output: dist\policy-helper\  (policy-helper.exe + _internal\ + baseline.json)
  Install by copying that folder to an admin-only location such as
  C:\Program Files\PolicyHelper\ and pointing Claude Code's policyHelper.path at
  the exe. The baseline holds site values (gateway URLs) and is never committed;
  start from baseline.example.json.

.PARAMETER Baseline
  Path to the site baseline.json.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\build.ps1 -Baseline C:\build\baseline.json
#>
param([Parameter(Mandatory = $true)][string]$Baseline)
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

if (-not (Test-Path -LiteralPath $Baseline -PathType Leaf)) {
    throw "baseline not found: $Baseline"
}

Write-Host '== 1/4 build venv (Python 3.11+)'
& py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required (py -3)' }
if (-not (Test-Path .build-venv)) { & py -3 -m venv .build-venv }
$Py = Join-Path $Here '.build-venv\Scripts\python.exe'
& $Py -m pip install --quiet --upgrade pip pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'pip install pyinstaller failed' }

Write-Host '== 2/4 PyInstaller onedir'
& $Py -m PyInstaller --noconfirm --onedir --name policy-helper --paths src `
    --distpath dist --workpath build --specpath build packaging\entry.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

Write-Host '== 3/4 place baseline.json next to the exe'
$Out = Join-Path $Here 'dist\policy-helper'
Copy-Item -LiteralPath $Baseline -Destination (Join-Path $Out 'baseline.json') -Force

Write-Host '== 4/4 smoke test (must print one envelope and exit 0)'
$Exe = Join-Path $Out 'policy-helper.exe'
$stdout = & $Exe
if ($LASTEXITCODE -ne 0) { throw "helper exited $LASTEXITCODE" }
$envelope = $stdout | ConvertFrom-Json
if ($null -eq $envelope.managedSettings) {
    throw 'helper printed no managedSettings - check the baseline (see stderr above)'
}
Write-Host "OK  $Exe"
