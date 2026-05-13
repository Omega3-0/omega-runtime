<#
.SYNOPSIS
  Generate SHA-256 release manifest for the shipped bundle.

.DESCRIPTION
  Walks $DistPath, computes SHA-256 for every binary artifact
  (*.exe, *.dll, *.pyd, *.so, *.dylib), and writes RELEASE-MANIFEST.txt
  with one line per file:

      <sha256>  <relative-path>

  Operators ship the manifest alongside the bundle. Users verify
  integrity via:

      Get-FileHash -Algorithm SHA256 <bundle>\Omega3.0-portable.exe

  and compare against the manifest line — works even if signing
  trust isn't established yet (new EV cert, internal CA, etc.).

  The manifest itself can also be Authenticode-signed via signtool
  (it's a text file; signing wraps it in a PKCS#7 envelope), but
  the common pattern is to publish it alongside a detached PGP
  signature when stronger trust is required.

.PARAMETER DistPath
  Folder containing shipped binaries (default: dist\Omega3.0-portable).

.PARAMETER OutFile
  Manifest path (default: <DistPath>\RELEASE-MANIFEST.txt).

.PARAMETER IncludeAll
  When set, hashes EVERY file in $DistPath (not just binaries) — useful
  for fully reproducible-build verification.

.EXAMPLE
  .\scripts\release_manifest.ps1
  .\scripts\release_manifest.ps1 -DistPath F:\releases\v0.1.0 -IncludeAll
#>
param(
    [string]$DistPath = "",
    [string]$OutFile = "",
    [switch]$IncludeAll
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
if (-not $DistPath) {
    $DistPath = Join-Path $Root "dist\Omega3.0-portable"
}
if (-not (Test-Path -LiteralPath $DistPath)) {
    Write-Error "DistPath not found: $DistPath"
    exit 1
}
if (-not $OutFile) {
    $OutFile = Join-Path $DistPath "RELEASE-MANIFEST.txt"
}

$BinaryExtensions = @(".exe", ".dll", ".pyd", ".so", ".dylib")
$rootFull = (Resolve-Path $DistPath).Path

$lines = New-Object System.Collections.Generic.List[string]
$header = @(
    "# Omega Runtime Studio release manifest",
    "# Generated: $(Get-Date -Format o)",
    "# DistPath: $DistPath",
    "# Format: <sha256>  <relative-path>",
    ""
)
foreach ($h in $header) { $lines.Add($h) | Out-Null }

$files = Get-ChildItem -Path $DistPath -Recurse -File | Sort-Object FullName
$included = 0
foreach ($file in $files) {
    if (-not $IncludeAll) {
        $ext = [System.IO.Path]::GetExtension($file.Name).ToLower()
        if ($BinaryExtensions -notcontains $ext) { continue }
    }
    # Skip the manifest itself — chicken-and-egg
    if ($file.FullName -eq $OutFile) { continue }
    $rel = $file.FullName.Substring($rootFull.Length).TrimStart([char[]]@('\', '/'))
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLower()
    $lines.Add("$hash  $rel") | Out-Null
    $included++
}

[System.IO.File]::WriteAllLines($OutFile, $lines)
Write-Host "Wrote manifest: $OutFile"
Write-Host "  $included file(s) hashed"
Write-Host ""
Write-Host "Verify a file:"
Write-Host "  Get-FileHash -Algorithm SHA256 <path> | Select Hash"
Write-Host "  (compare lowercase hex with manifest line)"
