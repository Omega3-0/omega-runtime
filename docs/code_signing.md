# Code signing & SmartScreen reputation

Goal: ship binaries that don't trigger Windows SmartScreen warnings or
antivirus false-positives on operator machines. Two halves to this:

1. **Authenticode signing** — proves who built the binary and that it
   hasn't been tampered with.
2. **SmartScreen reputation** — Microsoft's separate signal that "many
   people have run this signed binary, so it's probably fine."

Signing without reputation still triggers warnings. Reputation without
signing isn't possible. You need both.

---

## 1. Picking the right certificate

| Cert type | SmartScreen reputation | Cost / year | Use when |
|-----------|------------------------|-------------|----------|
| **OV** (Organization Validated) | Has to build over weeks of downloads | $200-500 | Internal teams, low-volume shipping, hobby projects |
| **EV** (Extended Validation) | Inherits immediate reputation | $300-700 | Public release where you can't wait for warnings to clear |

Recommend EV for any external-facing release. OV is fine for internal
tools (ops just clicks through SmartScreen once).

EV certs ship on a hardware token (USB / YubiKey). Signing requires the
token to be plugged in — that's the security trade-off. If you want
unattended CI signing, you'll need a cloud HSM (DigiCert KeyLocker,
SignPath, etc.) or a build host with the token attached.

OV certs come as a PFX file that can be loaded into any Windows cert
store or kept on disk with a password.

---

## 2. Build pipeline integration

The release-build script (`scripts/build_release.ps1`) detects signing
configuration from environment variables and fires signing automatically
when present. No flag changes needed — drop the right env vars and
re-run the build.

### Signing with a cert in the Windows cert store (recommended)

```powershell
$env:OMEGA_SIGN_CERT_THUMBPRINT = "ABC123...your cert SHA1..."
$env:OMEGA_SIGN_TIMESTAMP_URL = "http://timestamp.digicert.com"
.\scripts\build_release.ps1
```

### Signing with a PFX file (CI / offline)

```powershell
$env:OMEGA_SIGN_CERT_PATH = "C:\certs\codesign.pfx"
$env:OMEGA_SIGN_CERT_PASSWORD = "$env:CI_PFX_PASSWORD"  # from secret store
$env:OMEGA_SIGN_TIMESTAMP_URL = "http://timestamp.digicert.com"
.\scripts\build_release.ps1
```

The password is automatically redacted from the printed `signtool`
command — `<redacted>` in build logs — so PFX_PASS can't leak via
CI log capture.

### Signing internal DLLs too

By default we sign the two top-level EXEs (`Omega3.0-portable.exe`,
`Omega3.0-portable-Server.exe`). Some operators want every binary in
`_internal\` signed too (avoids the "this DLL is unsigned" log warnings
when ETW providers attach):

```powershell
$env:OMEGA_SIGN_INTERNAL_DLLS = "1"
.\scripts\build_release.ps1
```

DLL signing covers `*.dll` up to `OMEGA_SIGN_INTERNAL_DLL_DEPTH` levels
deep (default 2). Adds ~30s to the build for a typical PyInstaller
onedir output.

### Verification

`sign_windows.ps1` runs `signtool verify /pa <file>` after every signing
operation. If verification fails (corrupt sign, wrong cert, expired
timestamp), the script aborts with the underlying signtool exit code.

For manual post-build verification:

```powershell
signtool verify /pa dist\Omega3.0-portable\Omega3.0-portable.exe
signtool verify /pa dist\Omega3.0-portable\Omega3.0-portable-Server.exe
```

Successful output:

```
File: dist\Omega3.0-portable\Omega3.0-portable.exe
Index  Algorithm  Timestamp
========================================
0      sha256     RFC3161

Successfully verified: dist\Omega3.0-portable\Omega3.0-portable.exe
```

---

## 3. Release manifest

`scripts/release_manifest.ps1` runs automatically at the end of
`build_release.ps1` and writes
`dist\Omega3.0-portable\RELEASE-MANIFEST.txt`:

```
# Omega Runtime Studio release manifest
# Generated: 2026-05-13T...
# DistPath: F:\OmegaRuntimeStudio\dist\Omega3.0-portable
# Format: <sha256>  <relative-path>

abc123...  Omega3.0-portable-Server.exe
def456...  Omega3.0-portable.exe
...
```

Users verify integrity even without trusting the signing cert:

```powershell
# In bundle root
Get-FileHash -Algorithm SHA256 Omega3.0-portable.exe | Select Hash
# Compare lowercase hex against the manifest line
```

This catches:

- Disk corruption during distribution
- Tampered binaries from a man-in-the-middle download
- Repackaging by anyone other than the operator who built it

The manifest itself is plaintext and unsigned by default. For stronger
trust on the manifest, sign it with PGP (`gpg --detach-sign`) or with
your code-signing cert (`signtool sign /fd SHA256 ...`).

---

## 4. SmartScreen reputation

SmartScreen is Microsoft's reputation service for downloaded binaries.
Independent of code signing:

- **First-run warning** — SmartScreen warns on every signed binary
  until that binary has accumulated enough downloads + clean execution
  signals across enough machines.
- **OV certs** — reputation builds organically over weeks/months as
  users download and run the signed bundles.
- **EV certs** — Microsoft grants immediate reputation to binaries
  signed with EV certs (this is the main reason EV is recommended for
  public-facing releases).

What operators report seeing during the reputation-building period
(OV path):

> Windows protected your PC: Microsoft Defender SmartScreen prevented
> an unrecognized app from starting. Running this app might put your PC
> at risk. [More info] [Don't run]

Clicking "More info" → "Run anyway" is the only path through. After
enough operators do this, the warning eventually goes away.

If your release process can tolerate the EV cert token requirement, EV
is dramatically better operator UX for public releases. For internal
tools where you control the install path, OV is fine.

---

## 5. Antivirus false-positives

PyInstaller bundles get flagged by some AV products (notably less-known
European vendors and some on-prem corporate tools). Signed binaries get
fewer FPs but not zero. Mitigations:

- **Submit binary samples to AV vendors.** Most have an analyst-submit
  form; turnaround is 1-7 days for whitelisting.
- **Strip unused PyInstaller modules** in `omega_studio.spec`'s
  `excludes` list — smaller surface = fewer heuristic triggers.
- **Don't rebuild constantly without re-submitting.** AV vendors track
  signature hashes; bundle changes invalidate prior whitelisting.

---

## 6. Cert renewal

Code signing certs expire (typically 1-3 years). Two concerns at
renewal time:

1. **Timestamped signatures stay valid past cert expiration.**
   `sign_windows.ps1` always uses `/tr <timestamp_url>` so signatures
   include an RFC3161 countersignature — binaries you shipped before the
   cert expired continue to verify successfully forever. Critical: do
   NOT sign without `/tr` (or `OMEGA_SIGN_TIMESTAMP_URL`) — un-timestamped
   signatures become "untrusted" the moment the cert expires.

2. **New cert breaks SmartScreen reputation.** Each new cert (even
   from the same CA, same legal entity) starts with zero reputation.
   This is why EV certs are renewed rather than re-issued where possible.

If renewing with the same vendor: ask for a `re-issue` rather than a
`renew` — most CAs preserve the certificate identity across re-issues
which preserves SmartScreen reputation.

---

## 7. Quick checklist for v1 release

- [ ] EV cert purchased and installed (token plugged in, or PFX
      available with password in CI secret store)
- [ ] Build host has Windows SDK installed (`signtool` on PATH)
- [ ] `OMEGA_SIGN_*` env vars set in CI / release shell
- [ ] `.\scripts\build_release.ps1` succeeds, sign step fires, verify
      step passes
- [ ] `RELEASE-MANIFEST.txt` generated in `dist\Omega3.0-portable\`
- [ ] Manual test: `signtool verify /pa <exe>` from a different account
      on the same host (catches cert-store/permission issues)
- [ ] Manual test: download the zip + check hashes against
      `RELEASE-MANIFEST.txt`
- [ ] First-run SmartScreen behavior tested on a clean Windows host
      (EV: immediate trust; OV: "More info" path documented for
      operator handbook)
