<#
.SYNOPSIS
  Create .venv-portable using embedded python and install requirements.

.PARAMETER EmbeddedPython
  Path to embedded python.exe (default: <repo>\runtime\python\python.exe)

.PARAMETER VenvDir
  Virtual environment directory (default: <repo>\.venv-portable)

.PARAMETER Requirements
  requirements.txt path (default: repo root)

.PARAMETER LockFile
  Optional requirements-lock.txt — if present, installed after main requirements.

.EXAMPLE
  .\scripts\create_portable_venv.ps1
  .\scripts\create_portable_venv.ps1 -EmbeddedPython F:\dist\Omega3.0-portable\python\python.exe
#>
param(
    [string]$EmbeddedPython = "",
    [string]$VenvDir = "",
    [string]$Requirements = "",
    [string]$LockFile = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
if (-not $EmbeddedPython) { $EmbeddedPython = Join-Path $Root "runtime\python\python.exe" }
if (-not $VenvDir) { $VenvDir = Join-Path $Root ".venv-portable" }
if (-not $Requirements) { $Requirements = Join-Path $Root "requirements.txt" }
if (-not $LockFile) { $LockFile = Join-Path $Root "requirements-lock.txt" }

if (-not (Test-Path -LiteralPath $EmbeddedPython)) {
    Write-Error "Embedded python not found: $EmbeddedPython — run .\scripts\bootstrap_embedded_python.ps1 first"
    exit 1
}

function Get-RelativePath([string]$FromDir, [string]$ToPath) {
    $a = (New-Object System.Uri ($FromDir.TrimEnd('\') + '\'))
    $b = (New-Object System.Uri ([System.IO.Path]::GetFullPath($ToPath)))
    return [System.Uri]::UnescapeDataString($a.MakeRelativeUri($b).ToString()).Replace('/', '\')
}

$embedRoot = Split-Path $EmbeddedPython -Parent
$pth = Get-ChildItem -Path $embedRoot -Filter "python*._pth" -File -ErrorAction SilentlyContinue | Select-Object -First 1

function Add-PthLine([string]$pthPath, [string]$relativeLine) {
    $lines = @(Get-Content -LiteralPath $pthPath)
    foreach ($ln in $lines) {
        if ($ln.Trim() -eq $relativeLine.Trim()) { return }
    }
    Add-Content -LiteralPath $pthPath -Value $relativeLine -Encoding ascii
}

Write-Host "Creating venv at $VenvDir"
$venvPy = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPy)) {
    & $EmbeddedPython -m venv $VenvDir 2>&1 | Out-Host
    if (-not (Test-Path -LiteralPath $venvPy)) {
        Write-Host "venv module unavailable; installing virtualenv on embed..."
        & $EmbeddedPython -m pip install --upgrade pip virtualenv
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $EmbeddedPython -m virtualenv $VenvDir
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
}

$sitePackages = Join-Path $VenvDir "Lib\site-packages"
if ($pth -and (Test-Path -LiteralPath $sitePackages)) {
    try {
        $rel = Get-RelativePath -FromDir $embedRoot -ToPath $sitePackages
        Write-Host "Appending to $($pth.Name): $rel"
        Add-PthLine $pth.FullName $rel
    }
    catch {
        Write-Warning "Could not compute ._pth relative line: $_"
    }
}
else {
    Write-Warning "Skipping ._pth edit (missing ._pth or site-packages)."
}

Write-Host "Installing packages with $venvPy"
& $venvPy -m pip install --upgrade pip
if (Test-Path -LiteralPath $Requirements) {
    & $venvPy -m pip install -r $Requirements
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if (Test-Path -LiteralPath $LockFile) {
    & $venvPy -m pip install -r $LockFile
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $venvPy -m pip install -e $Root
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Portable venv ready: $venvPy"
