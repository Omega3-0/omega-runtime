<#
.SYNOPSIS
  Download official Windows amd64 embedded CPython and bootstrap pip.

.DESCRIPTION
  Fetches python-embed-amd64 from python.org into runtime\python\ (repo layout).
  Uncomments "import site" in python*._pth and runs get-pip.py so pip works.
  Requires outbound HTTPS (python.org + bootstrap.pypa.io).

.PARAMETER PythonVersion
  Full patch version, e.g. 3.12.8 or 3.11.9

.PARAMETER InstallDir
  Target directory (default: <repo>\runtime\python)

.EXAMPLE
  .\scripts\bootstrap_embedded_python.ps1
  .\scripts\bootstrap_embedded_python.ps1 -PythonVersion 3.11.9
#>
param(
    [string]$PythonVersion = "3.12.8",
    [string]$InstallDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
if (-not $InstallDir) {
    $InstallDir = Join-Path $Root "runtime\python"
}

$verParts = $PythonVersion.Split(".")
if ($verParts.Count -lt 2) {
    Write-Error "PythonVersion must be like 3.12.8"
    exit 1
}
$tag = "$($verParts[0]).$($verParts[1])"
$zipName = "python-embed-amd64-$PythonVersion.zip"
$zipUrl = "https://www.python.org/ftp/python/$PythonVersion/$zipName"
$getPipUrl = "https://bootstrap.pypa.io/get-pip.py"

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$zipPath = Join-Path $InstallDir $zipName
$getPipPath = Join-Path $InstallDir "get-pip.py"

Write-Host "Downloading $zipUrl -> $zipPath"
Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath
Expand-Archive -LiteralPath $zipPath -DestinationPath $InstallDir -Force
Remove-Item -LiteralPath $zipPath -Force

Write-Host "Downloading get-pip.py"
Invoke-WebRequest -Uri $getPipUrl -OutFile $getPipPath

$pyExe = Join-Path $InstallDir "python.exe"
if (-not (Test-Path -LiteralPath $pyExe)) {
    Write-Error "python.exe missing after extract in $InstallDir"
    exit 1
}

$pth = Get-ChildItem -Path $InstallDir -Filter "python*._pth" -File | Select-Object -First 1
if (-not $pth) {
    Write-Warning "No python*._pth found; pip may not see site-packages."
}
else {
    $lines = Get-Content -LiteralPath $pth.FullName
    $out = foreach ($line in $lines) {
        if ($line -match '^\s*#\s*import\s+site') { "import site" }
        else { $line }
    }
    Set-Content -LiteralPath $pth.FullName -Value $out -Encoding ascii
    Write-Host "Updated $($pth.Name) (import site enabled)"
}

Write-Host "Bootstrapping pip..."
& $pyExe $getPipPath --no-warn-script-location
if ($LASTEXITCODE -ne 0) {
    Write-Error "get-pip failed with exit $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Host "Done. Embedded interpreter: $pyExe"
Write-Host "Next: .\scripts\create_portable_venv.ps1 (adds venv site-packages to ._pth)"
