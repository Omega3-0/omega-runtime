<#
.SYNOPSIS
  Deprecated wrapper — calls sync_vendor_accelerators.ps1 with the same parameters.

.NOTES
  Prefer ``scripts\sync_vendor_accelerators.ps1``. Destination layout is ``vendor\accelerators\bin``.
#>
param(
    [string]$HarvestRoot = "F:\omega_runtime_harvest",
    [Parameter(Mandatory = $true)]
    [string]$VendorDest,
    [string]$PostBuildDist = ""
)
Write-Warning "sync_lemonade_vendor.ps1 is deprecated; use scripts\sync_vendor_accelerators.ps1"
& (Join-Path $PSScriptRoot "sync_vendor_accelerators.ps1") `
    -HarvestRoot $HarvestRoot `
    -VendorDest $VendorDest `
    -PostBuildDist $PostBuildDist
