<#
.SYNOPSIS
  Build Omega Runtime Studio Windows executables (PyInstaller); output folder remains dist\Omega3.0-portable.

.PARAMETER OneFile
  If set, GUI is built as a single --onefile exe (CLI remains onefile).

.PARAMETER Icon
  Optional path to .ico for both GUI and CLI exes (sets OMEGA_PYI_ICON).

.PARAMETER PostSyncVendor
  After build, robocopy vendor from this project root into dist (optional).

.PARAMETER DryRun
  Print commands only; do not install deps or invoke PyInstaller.

.PARAMETER UseEmbeddedPython
  Run scripts/bootstrap_embedded_python.ps1 (if needed) and scripts/create_portable_venv.ps1 (if needed),
  then build with .venv-portable\Scripts\python.exe.

.PARAMETER SkipSmoke
  Skip the post-build smoke gate (launching the CLI exe + probing /health).
  Useful for fast iterative work where you'll smoke manually anyway.
  Also bypassable via $env:OMEGA_BUILD_SKIP_SMOKE=1.

.EXAMPLE
  .\scripts\build_windows.ps1
  .\scripts\build_windows.ps1 -OneFile -Icon F:\icons\omega.ico
  .\scripts\build_windows.ps1 -UseEmbeddedPython
  .\scripts\build_windows.ps1 -SkipSmoke
#>
param(
    [switch]$OneFile,
    [string]$Icon = "",
    [string]$PostSyncVendor = "",
    [switch]$UseEmbeddedPython,
    [switch]$DryRun,
    [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

function Invoke-Line([string]$Line) {
    Write-Host $Line
    if ($DryRun) { return }
    # $ErrorActionPreference = "Stop" only halts on PowerShell-native errors;
    # native commands (python, pyinstaller, pip) that exit non-zero are
    # otherwise silently ignored, which lets a failed PyInstaller build
    # fall through to the smoke gate where it lies "OK" against the stale
    # Server.exe still on disk from a previous build. Reset + explicit
    # check shuts that path.
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

$PyExe = $null
if ($UseEmbeddedPython) {
    $embedPy = Join-Path $Root "runtime\python\python.exe"
    $venvPortable = Join-Path $Root ".venv-portable\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $embedPy)) {
        Write-Host "Embedding runtime not found; running bootstrap_embedded_python.ps1"
        Invoke-Line "& `"$PSScriptRoot\bootstrap_embedded_python.ps1`""
    }
    if (-not (Test-Path -LiteralPath $venvPortable)) {
        Write-Host "Portable venv missing; running create_portable_venv.ps1"
        Invoke-Line "& `"$PSScriptRoot\create_portable_venv.ps1`""
    }
    $PyExe = $venvPortable
}
elseif (Test-Path (Join-Path $Root ".venv\Scripts\python.exe")) {
    $PyExe = Join-Path $Root ".venv\Scripts\python.exe"
}
elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $PyExe = "py"
}
elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PyExe = "python"
}
if (-not $PyExe) {
    Write-Error "Python not found. Install Python 3.11+ or create .venv under $Root"
    exit 1
}

$DistBundle = Join-Path $Root "dist\Omega3.0-portable"
$ReqMain = Join-Path $Root "requirements.txt"
$ReqDev = Join-Path $Root "requirements-dev.txt"

if ($env:OMEGA_VARIANT) {
    $env:OMEGA_VARIANT = $env:OMEGA_VARIANT
} else {
    $env:OMEGA_VARIANT = "cpu"
}
$env:OMEGA_VARIANT = $env:OMEGA_VARIANT
# tarball for llama-cpp-python, which triggers a deep CMake + C++ compile
# AND fails on Windows MAX_PATH because llama.cpp's vendored Svelte tree
# (vendor/llama.cpp/tools/server/webui/src/lib/components/.../*.svelte)
# blows past the 260-char path limit during pip's tarball extraction.
#
# abetlen's GitHub Pages index serves pre-built wheels by Python version
# + accelerator variant:
#   /cpu/    — CPU-only (default; baseline shipped to Studio bundle)
#   /cu121/  — CUDA 12.1
#   /cu122/  — CUDA 12.2
#   /metal/  — macOS Metal
# Operators wanting GPU should pip-install via the matching extra-index
# AFTER the bundle is built, OR override $LlamaCppIndex before build.
$LlamaCppIndex = if ($env:OMEGA_LLAMA_CPP_INDEX) { $env:OMEGA_LLAMA_CPP_INDEX } else { "https://abetlen.github.io/llama-cpp-python/whl/cpu/" }

Invoke-Line "& `"$PyExe`" -m pip install --upgrade pip"
if (Test-Path $ReqMain) {
    Invoke-Line "& `"$PyExe`" -m pip install --extra-index-url `"$LlamaCppIndex`" -r `"$ReqMain`" pyinstaller"
}
else {
    Invoke-Line "& `"$PyExe`" -m pip install pyinstaller"
}

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

# ─────────────────────────────────────────────────────────────────
# Bake build-time variant into the package
# ─────────────────────────────────────────────────────────────────
# `OMEGA_VARIANT` env vars don't survive the daemon spawn (the
# subprocess inherits whatever was set when the daemon parent was
# launched, but the operator's shell env at that moment may not
# include the build-time choice). Writing a Python constant module
# here means PyInstaller bakes the value into the bundle; the
# runtime reads it via `omega_studio._build_info.BUILD_VARIANT`,
# falling back to the env var (so operator can still override
# at runtime) and then to "cpu".
$buildInfoPath = Join-Path $Root "src\omega_studio\_build_info.py"
$buildInfoContent = @"
"""Build-time metadata. Generated by scripts/build_windows.ps1.
Gitignored — dev runs without this file fall back to env var or 'cpu'."""
BUILD_VARIANT: str = "$($env:OMEGA_VARIANT)"
BUILD_TIMESTAMP: str = "$(Get-Date -Format o)"
"@
Write-Host "Writing build metadata: BUILD_VARIANT=$($env:OMEGA_VARIANT)"
if (-not $DryRun) {
    Set-Content -LiteralPath $buildInfoPath -Value $buildInfoContent -Encoding utf8
}

$specGui = Join-Path $Root "omega_studio.spec"
$specCli = Join-Path $Root "omega_studio_cli.spec"

$guiDistArg = "--distpath `"$DistBundle`""
if (-not $OneFile) {
    # Onedir: COLLECT name is Omega3.0-portable; keep default dist root so layout is dist\Omega3.0-portable\
    $guiDistArg = "--distpath `"$(Join-Path $Root 'dist')`""
}

Invoke-Line "& `"$PyExe`" -m PyInstaller --clean --noconfirm --workpath `"$(Join-Path $Root 'build\gui')`" $guiDistArg `"$specGui`""

Remove-Item Env:\OMEGA_PYI_ONEFILE -ErrorAction SilentlyContinue

Invoke-Line "& `"$PyExe`" -m PyInstaller --clean --noconfirm --workpath `"$(Join-Path $Root 'build\cli')`" --distpath `"$DistBundle`" `"$specCli`""

Remove-Item Env:\OMEGA_PYI_ICON -ErrorAction SilentlyContinue

if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path (Join-Path $DistBundle "models") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $DistBundle "vendor") | Out-Null
    Copy-Item -Force (Join-Path $Root "pyi_support\DIST_README.txt") (Join-Path $DistBundle "README-portable.txt")
    Copy-Item -Force (Join-Path $Root "pyi_support\run-server.bat") (Join-Path $DistBundle "run-server.bat")

    if ($PostSyncVendor) {
        $src = Join-Path $PostSyncVendor "vendor"
        $dst = Join-Path $DistBundle "vendor"
        if (Test-Path -LiteralPath $src) {
            Write-Host "robocopy vendor -> $dst"
            robocopy $src $dst /E /R:2 /W:2 /NFL /NDL /NJH /NJS
            if ($LASTEXITCODE -ge 8) {
                Write-Error "robocopy failed with code $LASTEXITCODE"
                exit $LASTEXITCODE
            }
        }
        else {
            Write-Warning "PostSyncVendor: source vendor folder not found: $src"
        }
    }
}

# ─────────────────────────────────────────────────────────────────
# Post-build smoke gate
# ─────────────────────────────────────────────────────────────────
# Build success ≠ bundle is functional. PyInstaller can produce an
# exe that's missing ctypes-loaded DLLs (llama_cpp/lib/llama.dll) or
# C-extension modules (_sqlite3.pyd) — pip-install + PyInstaller
# analysis both complete with exit 0, but the bundle 500s on first
# real use. This session shipped TWO such bundles before catching it
# manually. The smoke gate makes "build succeeded" mean "bundle
# actually starts and serves /health".
#
# Smoke = launch the CLI exe on a random high port, wait for /health
# to return 200, then daemon-stop. ~5s total. Catches:
#   - Missing llama_cpp libs (server fails to import)
#   - Missing _sqlite3 (hub_jobs import fails → app fails to start)
#   - Bad spec hidden imports (any module missing at runtime)
#   - Port-bind regressions
#   - Anything else that breaks "server can serve /health"
#
# Skip via -SkipSmoke or $env:OMEGA_BUILD_SKIP_SMOKE=1 for fast
# iterative work where you'll smoke manually anyway.
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
                if ($health.StatusCode -eq 200) {
                    $smokeOk = $true
                    if ($env:OMEGA_VARIANT -ne "cpu") {
                        # GPU variant gate: the wheel must actually support
                        # GPU offload (not just have the SDK installed). The
                        # backend info is nested on /health under
                        # `.backend.llama_supports_gpu_offload` — checking
                        # only `gpu_acceleration_detected` returns false
                        # positives because that flag goes true whenever a
                        # GPU SDK env var is set, even if the shipped wheel
                        # is CPU-only. `llama_supports_gpu_offload=true` is
                        # the truth.
                        if ($health.Content -notmatch '"llama_supports_gpu_offload":\s*true') {
                            Write-Host "Smoke gate warning: GPU variant ($($env:OMEGA_VARIANT)) bundle did not report llama_supports_gpu_offload=true."
                            Write-Host "  (Check that the bundled llama-cpp-python wheel was actually built with GPU support.)"
                            $smokeOk = $false
                        }
                    }
                    break
                }
            } catch { Start-Sleep -Milliseconds 500 }
        }
        if ($smokeOk) {
            Write-Host "Smoke gate: OK (bundle serves /health)"
            # Mark the smoke log as success
            "OK" | Out-File "$smokeLog.ok" -Encoding ascii
        } else {
            Write-Host ""
            Write-Host "============================================================"
            Write-Host "Smoke gate FAILED: bundle did not serve /health within 30s"
            Write-Host "  - Server.exe path: $serverExe"
            Write-Host "  - Captured log: $smokeLog"
            Write-Host "  - Captured stderr: $smokeLog.err"
            if (Test-Path "$smokeLog.err") {
                Write-Host "============================================================"
                Write-Host "STDERR tail (last 30 lines):"
                Get-Content "$smokeLog.err" -Tail 30
            }
            Write-Host "============================================================"
            # Rename the broken exe so it can't accidentally be shipped
            $brokenName = "$serverExe.broken"
            try { Move-Item -Force $serverExe $brokenName } catch {}
            exit 2
        }
    } finally {
        if ($smokeProc -and -not $smokeProc.HasExited) {
            try { Stop-Process -Id $smokeProc.Id -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
}

Write-Host ""
Write-Host "Done. Bundle folder: $DistBundle"
Write-Host '  GUI: Omega3.0-portable.exe (windowed; onedir _internal unless -OneFile)'
Write-Host '  CLI: Omega3.0-portable-Server.exe (console; Typer serve/daemon)'
