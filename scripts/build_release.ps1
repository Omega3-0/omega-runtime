<#
.SYNOPSIS
  Retail-oriented Omega Runtime Studio release build: clean tree, isolated venv, tests, PyInstaller, lock file.

.DESCRIPTION
  - Removes build/, dist/, and optionally .venv-release/
  - Creates .venv-release from system ``py -3.11`` or ``-PythonExe``
  - pip install -r requirements.txt (+ pyinstaller, pytest, dev tools for the gate)
  - pytest
  - PyInstaller for GUI + CLI (same layout as build_windows.ps1)
  - Writes requirements-lock.txt from pip freeze after success

.PARAMETER PythonExe
  Explicit python.exe for the release venv (default: ``py -3.11`` launcher).

.PARAMETER SkipRecycleVenv
  Keep existing .venv-release instead of deleting it first.

.PARAMETER OneFile
  Same as build_windows.ps1 — onefile GUI.

.PARAMETER Icon
  Optional .ico path (OMEGA_PYI_ICON).

.PARAMETER PostSyncVendor
  Optional project root whose vendor/ is robocopied into dist after build.

.PARAMETER DryRun
  Print planned steps only.

.PARAMETER PrototypeBundle
  After a successful build, run ``scripts\build_prototype_bundle.ps1`` against ``dist\Omega3.0-portable``.

.PARAMETER PrototypeBundleEmbed
  Pass ``-BundleEmbed`` to the prototype script (copy ``runtime\python`` when present).

.PARAMETER PrototypeSkipPipTarget
  Pass ``-SkipPipTarget`` to the prototype script (skip ``pip install --target``).

.NOTES
  Gaps: does not install Windows SDK / signtool; signing is scripts/sign_windows.ps1.
  ``--exclude-module`` duplicates are safe; spec Analysis ``excludes`` also trims junk.
#>
param(
    [string]$PythonExe = "",
    [switch]$SkipRecycleVenv,
    [switch]$OneFile,
    [string]$Icon = "",
    [string]$PostSyncVendor = "",
    [switch]$DryRun,
    [switch]$PrototypeBundle,
    [switch]$PrototypeBundleEmbed,
    [switch]$PrototypeSkipPipTarget,
    [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$VenvDir = Join-Path $Root ".venv-release"
$PyLauncher = "py"
if ($PythonExe) {
    if (-not (Test-Path -LiteralPath $PythonExe)) {
        Write-Error "PythonExe not found: $PythonExe"
        exit 1
    }
    $BootstrapPy = $PythonExe
}
else {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        Write-Error "Python launcher 'py' not found; pass -PythonExe path\\to\\python.exe"
        exit 1
    }
    $BootstrapPy = "$PyLauncher -3.11"
}

function Invoke-Line([string]$Line) {
    Write-Host $Line
    if ($DryRun) { return }
    # Mirror of the build_windows.ps1 hardening: native-command exits
    # are silently dropped by `Invoke-Expression`, so a failed
    # PyInstaller / pip step otherwise sails through to the smoke
    # gate which then runs against a stale binary and lies "OK".
    # Reset + explicit $LASTEXITCODE check shuts that path.
    $global:LASTEXITCODE = 0
    Invoke-Expression $Line
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "============================================================"
        Write-Host "Build step failed with exit code $LASTEXITCODE"
        Write-Host "Command: $Line"
        Write-Host "============================================================"
        exit $LASTEXITCODE
    }
}

Write-Host "Cleaning build/, dist/"
if (-not $DryRun) {
    foreach ($p in @("build", "dist")) {
        $full = Join-Path $Root $p
        if (Test-Path $full) {
            Remove-Item -Recurse -Force $full
        }
    }
}

if (-not $SkipRecycleVenv) {
    Write-Host "Recycling $VenvDir"
    if (-not $DryRun -and (Test-Path $VenvDir)) {
        Remove-Item -Recurse -Force $VenvDir
    }
}

Write-Host "Creating venv -> $VenvDir"
if ($PythonExe) {
    Invoke-Line "& `"$PythonExe`" -m venv `"$VenvDir`""
}
else {
    Invoke-Line "& py -3.11 -m venv `"$VenvDir`""
}

$PyRel = Join-Path $VenvDir "Scripts\python.exe"
if (-not $DryRun -and -not (Test-Path $PyRel)) {
    Write-Error "Release venv python missing: $PyRel"
    exit 1
}

Invoke-Line "& `"$PyRel`" -m pip install --upgrade pip"
$Req = Join-Path $Root "requirements.txt"
$ReqDev = Join-Path $Root "requirements-dev.txt"

# Pre-built wheel index for llama-cpp-python (avoids Windows MAX_PATH on
# source extraction). Override via OMEGA_LLAMA_CPP_INDEX for GPU variants
# (cu121/cu122/metal). See build_windows.ps1 for full rationale.
$LlamaCppIndex = if ($env:OMEGA_LLAMA_CPP_INDEX) { $env:OMEGA_LLAMA_CPP_INDEX } else { "https://abetlen.github.io/llama-cpp-python/whl/cpu/" }
Invoke-Line "& `"$PyRel`" -m pip install --extra-index-url `"$LlamaCppIndex`" -r `"$Req`" -r `"$ReqDev`" pyinstaller"

Invoke-Line "& `"$PyRel`" -m pytest"

# Documented smallest-tree excludes (safe when not imported by app; spec also lists excludes).
$ExMod = @(
    "matplotlib", "tkinter", "test", "unittest", "pydoc", "xmlrpc",
    "IPython", "jupyter", "notebook", "pytest"
) | ForEach-Object { "--exclude-module", $_ }
$ExModLine = $ExMod -join " "

if ($Icon) {
    $env:OMEGA_PYI_ICON = $Icon
}
else {
    Remove-Item Env:\OMEGA_PYI_ICON -ErrorAction SilentlyContinue
}

if ($OneFile) {
    $env:OMEGA_PYI_ONEFILE = "1"
}
else {
    Remove-Item Env:\OMEGA_PYI_ONEFILE -ErrorAction SilentlyContinue
}

$DistBundle = Join-Path $Root "dist\Omega3.0-portable"
$specGui = Join-Path $Root "omega_studio.spec"
$specCli = Join-Path $Root "omega_studio_cli.spec"
$guiDistArg = "--distpath `"$DistBundle`""
if (-not $OneFile) {
    $guiDistArg = "--distpath `"$(Join-Path $Root 'dist')`""
}

Invoke-Line "& `"$PyRel`" -m PyInstaller --clean --noconfirm $ExModLine --workpath `"$(Join-Path $Root 'build\gui')`" $guiDistArg `"$specGui`""

Remove-Item Env:\OMEGA_PYI_ONEFILE -ErrorAction SilentlyContinue
Invoke-Line "& `"$PyRel`" -m PyInstaller --clean --noconfirm $ExModLine --workpath `"$(Join-Path $Root 'build\cli')`" --distpath `"$DistBundle`" `"$specCli`""
Remove-Item Env:\OMEGA_PYI_ICON -ErrorAction SilentlyContinue

if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path (Join-Path $DistBundle "models") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $DistBundle "vendor") | Out-Null
    Copy-Item -Force (Join-Path $Root "pyi_support\DIST_README.txt") (Join-Path $DistBundle "README-portable.txt")
    if ($PostSyncVendor) {
        $src = Join-Path $PostSyncVendor "vendor"
        $dst = Join-Path $DistBundle "vendor"
        if (Test-Path -LiteralPath $src) {
            robocopy $src $dst /E /R:2 /W:2 /NFL /NDL /NJH /NJS
            if ($LASTEXITCODE -ge 8) {
                Write-Error "robocopy failed with code $LASTEXITCODE"
                exit $LASTEXITCODE
            }
        }
        else {
            Write-Warning "PostSyncVendor: vendor not found: $src"
        }
    }
    $lockPath = Join-Path $Root "requirements-lock.txt"
    & $PyRel -m pip freeze | Set-Content -Encoding utf8 $lockPath
    Write-Host "Wrote $lockPath"
}

Write-Host "Release build complete -> $DistBundle"

# Post-build smoke gate — see build_windows.ps1 for full rationale.
# Release builds are even more important to gate than dev builds since
# they're what reaches operators. A pytest pass + clean PyInstaller
# exit is NOT enough to prove the bundle works — ctypes-loaded DLLs
# and C-extension modules can still be missing.
if (-not $DryRun -and -not $SkipSmoke -and -not $env:OMEGA_BUILD_SKIP_SMOKE) {
    $serverExe = Join-Path $DistBundle "Omega3.0-portable-Server.exe"
    if (-not (Test-Path -LiteralPath $serverExe)) {
        Write-Error "Smoke gate: $serverExe not found after build"
        exit 1
    }
    $smokePort = Get-Random -Minimum 13100 -Maximum 13900
    $smokeLog = Join-Path $DistBundle "build-smoke.log"
    Write-Host ""
    Write-Host "Smoke-testing bundle on 127.0.0.1:$smokePort ..."
    $smokeProc = Start-Process -FilePath $serverExe `
        -ArgumentList "serve","--host","127.0.0.1","--port","$smokePort","--log-level","warning" `
        -RedirectStandardOutput $smokeLog `
        -RedirectStandardError "$smokeLog.err" `
        -PassThru -WindowStyle Hidden
    try {
        $smokeOk = $false
        $deadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $deadline) {
            try {
                $health = Invoke-WebRequest -Uri "http://127.0.0.1:$smokePort/health" `
                    -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
                if ($health.StatusCode -eq 200) { $smokeOk = $true; break }
            } catch { Start-Sleep -Milliseconds 500 }
        }
        if ($smokeOk) {
            Write-Host "Smoke gate: OK (release bundle serves /health)"
            "OK" | Out-File "$smokeLog.ok" -Encoding ascii
        } else {
            Write-Host "============================================================"
            Write-Host "Smoke gate FAILED: release bundle did not serve /health in 30s"
            Write-Host "  - Captured stderr: $smokeLog.err"
            if (Test-Path "$smokeLog.err") {
                Get-Content "$smokeLog.err" -Tail 30
            }
            Write-Host "============================================================"
            try { Move-Item -Force $serverExe "$serverExe.broken" } catch {}
            exit 2
        }
    } finally {
        if ($smokeProc -and -not $smokeProc.HasExited) {
            try { Stop-Process -Id $smokeProc.Id -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
}

# ─────────────────────────────────────────────────────────────────
# Optional Authenticode signing (fires when cert env vars are set)
# ─────────────────────────────────────────────────────────────────
# Signing is opt-in via environment variables — release-build hosts
# without a cert (developer workstations, CI matrix steps that don't
# need signed output) skip silently. Two paths:
#
#   $env:OMEGA_SIGN_CERT_THUMBPRINT — cert already in Windows store
#   $env:OMEGA_SIGN_CERT_PATH + OMEGA_SIGN_CERT_PASSWORD — PFX file
#
# Optional knobs:
#   $env:OMEGA_SIGN_TIMESTAMP_URL — RFC3161 timestamp server
#   $env:OMEGA_SIGN_INTERNAL_DLLS=1 — also sign DLLs under _internal\
#
# Smoke gate runs BEFORE signing so a broken bundle can't ship signed.
# Signature verification runs INSIDE sign_windows.ps1 (`signtool verify
# /pa` after every signing) — a verify-fail aborts with non-zero exit.
$signRequested = $env:OMEGA_SIGN_CERT_THUMBPRINT -or $env:OMEGA_SIGN_CERT_PATH
if (-not $DryRun -and $signRequested) {
    Write-Host ""
    Write-Host "Authenticode signing requested via env vars..."
    $signScript = Join-Path $PSScriptRoot "sign_windows.ps1"
    $signSplat = @{ DistPath = $DistBundle }
    if ($env:OMEGA_SIGN_CERT_THUMBPRINT) {
        $signSplat.CertThumbprint = $env:OMEGA_SIGN_CERT_THUMBPRINT
    }
    elseif ($env:OMEGA_SIGN_CERT_PATH) {
        $signSplat.CertPath = $env:OMEGA_SIGN_CERT_PATH
        if ($env:OMEGA_SIGN_CERT_PASSWORD) {
            $signSplat.CertPassword = $env:OMEGA_SIGN_CERT_PASSWORD
        }
    }
    if ($env:OMEGA_SIGN_TIMESTAMP_URL) {
        $signSplat.TimestampUrl = $env:OMEGA_SIGN_TIMESTAMP_URL
    }
    if ($env:OMEGA_SIGN_INTERNAL_DLLS) {
        $signSplat.SignInternalDlls = $true
    }
    & $signScript @signSplat
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Signing failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
}
elseif (-not $DryRun) {
    Write-Host ""
    Write-Host "Skipping Authenticode signing (set OMEGA_SIGN_CERT_THUMBPRINT or OMEGA_SIGN_CERT_PATH to enable)"
}

# ─────────────────────────────────────────────────────────────────
# Release manifest — SHA-256 of every shipped binary
# ─────────────────────────────────────────────────────────────────
# Always generated, even on unsigned builds. Lets users verify
# integrity (matches the binary the operator built) independently
# of whether they trust the cert / signing infrastructure. The
# manifest is generated AFTER signing so the hashes reflect the
# final signed binaries.
if (-not $DryRun) {
    $manifestScript = Join-Path $PSScriptRoot "release_manifest.ps1"
    & $manifestScript -DistPath $DistBundle
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Release manifest generation failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
}

if ($PrototypeBundle) {
    $proto = Join-Path $PSScriptRoot "build_prototype_bundle.ps1"
    Write-Host "Running prototype bundle script: $proto"
    if (-not $DryRun) {
        $splat = @{
            RepoRoot    = $Root
            DistDir     = $DistBundle
            PythonExe   = $PyRel
        }
        if ($PrototypeBundleEmbed) {
            $splat.BundleEmbed = $true
        }
        if ($PrototypeSkipPipTarget) {
            $splat.SkipPipTarget = $true
        }
        & $proto @splat
    }
}
