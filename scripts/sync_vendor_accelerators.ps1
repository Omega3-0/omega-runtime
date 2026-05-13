<#
.SYNOPSIS
  Sync harvested accelerator binaries into Omega Runtime Studio (vendor\accelerators\bin).

.DESCRIPTION
  Copies from your local harvest tree (default F:\omega_runtime_harvest) into the project
  or portable bundle. The harvest layout may still use a top-level ``lemonade\bin`` folder
  on disk — that is only the *source* path name; Studio installs binaries under
  ``vendor\accelerators\bin`` (no third-party server is involved).

.PARAMETER HarvestRoot
  Directory whose child ``lemonade\bin`` contains DLLs/exes to copy (default F:\omega_runtime_harvest).

.PARAMETER VendorDest
  Destination project root (receives vendor\accelerators\bin).

.PARAMETER PostBuildDist
  Optional: after syncing into VendorDest, mirror vendor\ into a PyInstaller output folder
  (e.g. dist\Omega3.0-portable\vendor) for a portable drop.

.EXAMPLE
  .\scripts\sync_vendor_accelerators.ps1 -VendorDest F:\OmegaRuntimeStudio
  .\scripts\sync_vendor_accelerators.ps1 -VendorDest F:\OmegaRuntimeStudio -PostBuildDist F:\OmegaRuntimeStudio\dist\Omega3.0-portable
#>
param(
    [string]$HarvestRoot = "F:\omega_runtime_harvest",
    [Parameter(Mandatory = $true)]
    [string]$VendorDest,
    [string]$PostBuildDist = ""
)

$src = Join-Path $HarvestRoot "lemonade\bin"
if (-not (Test-Path -LiteralPath $src)) {
    Write-Error "Source not found: $src"
    exit 1
}

$dst = Join-Path $VendorDest "vendor\accelerators\bin"
New-Item -ItemType Directory -Force -Path $dst | Out-Null

Write-Host "Syncing $src -> $dst"
robocopy $src $dst /MIR /R:2 /W:2 /NFL /NDL /NJH /NJS
if ($LASTEXITCODE -ge 8) {
    Write-Error "robocopy failed with code $LASTEXITCODE"
    exit $LASTEXITCODE
}
Write-Host "Done. Set OMEGA_BUNDLE_ROOT=$VendorDest when running Omega Runtime Studio / API."

if ($PostBuildDist) {
    $srcRoot = Join-Path $VendorDest "vendor"
    $dstRoot = Join-Path $PostBuildDist "vendor"
    if (-not (Test-Path -LiteralPath $srcRoot)) {
        Write-Warning "PostBuildDist skip: no vendor at $srcRoot"
        exit 0
    }
    New-Item -ItemType Directory -Force -Path $dstRoot | Out-Null
    Write-Host "Post-build: robocopy $srcRoot -> $dstRoot"
    robocopy $srcRoot $dstRoot /E /R:2 /W:2 /NFL /NDL /NJH /NJS
    if ($LASTEXITCODE -ge 8) {
        Write-Error "robocopy failed with code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
}
