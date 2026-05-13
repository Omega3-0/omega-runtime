<#
.SYNOPSIS
  After PyInstaller, optionally assemble a prototype self-contained bundle for Omega Runtime Studio

.DESCRIPTION
  - Optional: robocopy embedded ``runtime/python`` into ``dist/.../python`` (-BundleEmbed).
  - ``pip install --target`` into ``dist/.../python/Lib/site-packages`` from requirements.txt.
  - Writes ``set_env.bat`` and ``Omega3.0-portable.env.example`` with bundle-relative hints.

.PARAMETER RepoRoot
  Repository root (default: parent of ``scripts``).

.PARAMETER DistDir
  Portable output folder (default: ``dist\Omega3.0-portable``).

.PARAMETER PythonExe
  Python used for ``pip install --target`` (e.g. release venv or system 3.11).

.PARAMETER BundleEmbed
  Copy ``runtime\python`` from repo into ``DistDir\python`` when present.

.PARAMETER SkipPipTarget
  Skip ``pip install --target`` (only layout/env files).

.PARAMETER DryRun
  Print actions only.
#>
param(
    [string]$RepoRoot = "",
    [string]$DistDir = "",
    [string]$PythonExe = "",
    [switch]$BundleEmbed,
    [switch]$SkipPipTarget,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
if (-not $RepoRoot) {
    $RepoRoot = Split-Path $PSScriptRoot -Parent
}
if (-not $DistDir) {
    $DistDir = Join-Path $RepoRoot "dist\Omega3.0-portable"
}
if (-not $PythonExe) {
    $PyRelease = Join-Path $RepoRoot ".venv-release\Scripts\python.exe"
    $PyPortable = Join-Path $RepoRoot ".venv-portable\Scripts\python.exe"
    if (Test-Path -LiteralPath $PyRelease) {
        $PythonExe = $PyRelease
    }
    elseif (Test-Path -LiteralPath $PyPortable) {
        $PythonExe = $PyPortable
    }
    else {
        $PythonExe = "py"
    }
}

function Run-Line([string]$Line) {
    Write-Host $Line
    if (-not $DryRun) {
        Invoke-Expression $Line
    }
}

if (-not (Test-Path -LiteralPath $DistDir)) {
    Write-Error "DistDir not found (run PyInstaller first): $DistDir"
    exit 1
}

$pyTree = Join-Path $DistDir "python"
$site = Join-Path $pyTree "Lib\site-packages"
$runtimeSrc = Join-Path $RepoRoot "runtime\python"

if ($BundleEmbed) {
    if (-not (Test-Path -LiteralPath $runtimeSrc)) {
        Write-Warning "BundleEmbed: source missing: $runtimeSrc (run scripts\bootstrap_embedded_python.ps1)"
    }
    else {
        Write-Host "Robocopy embedded runtime -> $pyTree"
        if (-not $DryRun) {
            New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
            robocopy $runtimeSrc $pyTree /E /R:2 /W:2 /NFL /NDL /NJH /NJS
            if ($LASTEXITCODE -ge 8) {
                Write-Error "robocopy failed: $LASTEXITCODE"
                exit $LASTEXITCODE
            }
        }
    }
}

if (-not $SkipPipTarget) {
    Write-Host "pip install --target -> $site"
    if (-not $DryRun) {
        New-Item -ItemType Directory -Force -Path $site | Out-Null
        $req = Join-Path $RepoRoot "requirements.txt"
        if (-not (Test-Path -LiteralPath $req)) {
            Write-Error "requirements.txt not found: $req"
            exit 1
        }
        if ($PythonExe -eq "py") {
            Run-Line "& py -3.11 -m pip install -r `"$req`" -t `"$site`" --upgrade"
        }
        else {
            Run-Line "& `"$PythonExe`" -m pip install -r `"$req`" -t `"$site`" --upgrade"
        }
    }
}

$setEnv = Join-Path $DistDir "set_env.bat"
$envEx = Join-Path $DistDir "Omega3.0-portable.env.example"
$setContent = @"
@echo off
REM Prototype bundle — run before starting exes from a plain cmd shell.
set "OMEGA_BUNDLE_ROOT=%~dp0"
set "PATH=%~dp0vendor\accelerators\bin\llamacpp\vulkan;%PATH%"
set "PATH=%~dp0vendor\accelerators\bin;%PATH%"
REM Optional: set CUDA toolkit if installed (uncomment and adjust)
REM set "CUDA_PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4"
REM set "PATH=%CUDA_PATH%\bin\x64;%PATH%"
"@
$exampleContent = @"
# Copy to Omega3.0-portable.env and load with your launcher, or merge into system env.
OMEGA_BUNDLE_ROOT=%~dp0
PATH=%~dp0vendor\accelerators\bin\llamacpp\vulkan;%~dp0vendor\accelerators\bin;%PATH%
# OMEGA_RUNTIME_HARVEST=
# OMEGA_API_KEY=
# OMEGA_GATEWAY_GGUF_WORKERS=2
# OMEGA_ORT_EP_ORDER=CUDAExecutionProvider,DmlExecutionProvider,CPUExecutionProvider
"@

if (-not $DryRun) {
    Set-Content -Path $setEnv -Value $setContent -Encoding ascii
    Set-Content -Path $envEx -Value $exampleContent -Encoding utf8
    Write-Host "Wrote $setEnv"
    Write-Host "Wrote $envEx"
}
else {
    Write-Host "[DryRun] would write set_env.bat and Omega3.0-portable.env.example"
}

Write-Host "Prototype bundle layout step complete."
