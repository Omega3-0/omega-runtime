<#
.SYNOPSIS
  Orchestrate variant builds (CPU, CUDA, Vulkan, etc.).

.DESCRIPTION
  Runs scripts\build_release.ps1 for each variant by setting OMEGA_LLAMA_CPP_INDEX.
  Bundles outputs into dist\Omega3.0-portable-<variant>-<version>.zip.

.EXAMPLE
  .\scripts\build_variants.ps1
#>
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$variants = @(
    @{ name = "cpu";    index = "https://abetlen.github.io/llama-cpp-python/whl/cpu/" },
    @{ name = "cuda";   index = "https://abetlen.github.io/llama-cpp-python/whl/cu122/" },
    @{ name = "vulkan"; index = "https://your-custom-vulkan-wheel-url/" } # Placeholder
)

$version = "0.1.0" # Should ideally be read from pyproject.toml

foreach ($var in $variants) {
    Write-Host "--- Building variant: $($var.name) ---"
    $env:OMEGA_LLAMA_CPP_INDEX = $var.index
    $env:OMEGA_VARIANT = $var.name
    
    # Run build
    $reqFile = Join-Path $Root "requirements-$($var.name).txt"
    if (Test-Path $reqFile) {
        $env:OMEGA_REQUIREMENTS = $reqFile
    } else {
        $env:OMEGA_REQUIREMENTS = Join-Path $Root "requirements.txt"
    }
    & "$PSScriptRoot\build_windows.ps1" -UseEmbeddedPython
    
    # Bundle output
    $outDir = Join-Path $Root "dist\Omega3.0-portable-$($var.name)-$version"
    # Logic to zip or move would go here
    Write-Host "Variant $($var.name) built to $outDir"
}
