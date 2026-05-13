<#
.SYNOPSIS
  Authenticode-sign Omega Runtime Studio portable exes and optionally DLLs under _internal.

.PARAMETER DistPath
  Folder containing Omega3.0-portable.exe (default: dist\Omega3.0-portable under repo).

.PARAMETER CertPath
  PFX path (use with -CertPassword). Ignored if -CertThumbprint is set.

.PARAMETER CertPassword
  PFX password (plain text — prefer CI secret store in real pipelines).

.PARAMETER CertThumbprint
  SHA1 thumbprint of cert already in Windows cert store (uses /sha1).

.PARAMETER TimestampUrl
  RFC3161 timestamp (e.g. http://timestamp.digicert.com).

.PARAMETER SignInternalDlls
  When set, signs *.dll under _internal up to -DllDepth directory levels.

.PARAMETER DllDepth
  Max depth under _internal for DLL signing (default 2).

.PARAMETER DryRun
  Print signtool commands only.

.EXAMPLE
  .\scripts\sign_windows.ps1 -CertPath C:\certs\codesign.pfx -CertPassword $env:PFX_PASS -TimestampUrl http://timestamp.digicert.com

.EXAMPLE
  .\scripts\sign_windows.ps1 -CertThumbprint ABC123... -SignInternalDlls

.NOTES
  Requires Windows SDK ``signtool`` on PATH. Verify after signing:
    signtool verify /pa dist\Omega3.0-portable\Omega3.0-portable.exe
#>
param(
    [string]$DistPath = "",
    [string]$CertPath = "",
    [string]$CertPassword = "",
    [string]$CertThumbprint = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [switch]$SignInternalDlls,
    [int]$DllDepth = 2,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
if (-not $DistPath) {
    $DistPath = Join-Path $Root "dist\Omega3.0-portable"
}

$SignTool = Get-Command signtool -ErrorAction SilentlyContinue
if (-not $SignTool) {
    Write-Error "signtool not on PATH. Install Windows SDK and use 'x64 Native Tools' / add SDK bin."
    exit 1
}

function Sign-One([string]$File) {
    if (-not (Test-Path -LiteralPath $File)) {
        Write-Warning "skip missing $File"
        return
    }
    $base = "signtool sign /fd SHA256 /td SHA256 /tr `"$TimestampUrl`""
    if ($CertThumbprint) {
        $cmd = "$base /sha1 $CertThumbprint `"$File`""
    }
    elseif ($CertPath) {
        if (-not $CertPassword) {
            Write-Error "CertPassword required when using CertPath"
            exit 1
        }
        $cmd = "$base /f `"$CertPath`" /p `"$CertPassword`" `"$File`""
    }
    else {
        Write-Error "Provide -CertThumbprint or -CertPath (+ -CertPassword)"
        exit 1
    }
    # Redact the password from the printed command — keeps PFX_PASS out
    # of CI logs even when operators paste them for triage.
    $displayCmd = $cmd
    if ($CertPassword) {
        $displayCmd = $cmd.Replace("/p `"$CertPassword`"", "/p `"<redacted>`"")
    }
    Write-Host $displayCmd
    if (-not $DryRun) {
        Invoke-Expression $cmd
        if ($LASTEXITCODE -ne 0) {
            Write-Error "signtool failed for $File exit=$LASTEXITCODE"
            exit $LASTEXITCODE
        }
        # Verify the signature on the file we just signed. Without this
        # check, a successful sign + corrupted exe can ship — the only
        # downstream signal is end-user SmartScreen warnings, which is
        # way too late to catch.
        $verify = "signtool verify /pa `"$File`""
        Write-Host $verify
        Invoke-Expression $verify
        if ($LASTEXITCODE -ne 0) {
            Write-Error "signtool verify failed for $File exit=$LASTEXITCODE"
            exit $LASTEXITCODE
        }
    }
}

$exes = @(
    (Join-Path $DistPath "Omega3.0-portable.exe"),
    (Join-Path $DistPath "Omega3.0-portable-Server.exe")
)
foreach ($e in $exes) {
    Sign-One $e
}

if ($SignInternalDlls) {
    $internal = Join-Path $DistPath "_internal"
    if (Test-Path $internal) {
        $rootFull = (Resolve-Path $internal).Path
        Get-ChildItem -Path $internal -Recurse -Filter "*.dll" -File | ForEach-Object {
            $dir = $_.DirectoryName
            $rel = ""
            if ($dir.Length -gt $rootFull.Length) {
                $rel = $dir.Substring($rootFull.Length).TrimStart([char[]]@('\', '/'))
            }
            $depth = 0
            if ($rel) {
                $depth = ($rel -split '[\\/]', 0, "SimpleMatch" | Where-Object { $_ }).Count
            }
            if ($depth -le $DllDepth) {
                Sign-One $_.FullName
            }
        }
    }
    else {
        Write-Warning "_internal not found (onefile GUI build?); no DLL batch signing."
    }
}

Write-Host "Done. Optional verify:"
Write-Host "  signtool verify /pa `"$(Join-Path $DistPath 'Omega3.0-portable.exe')`""
