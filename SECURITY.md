# Security Policy

## Supported versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | Yes (current)      |
| < 0.1   | No                 |

## Reporting a vulnerability

**Do not file a public issue for a security report.** Public disclosure
before a patch is available puts every operator at risk.

Report privately by emailing the maintainers at:

> `security@omega3-0` — replace with the operator's published contact.
> (Maintainers: set up a forwarder before the public release.)

Include:

- A description of the issue and where to find it (file path, endpoint,
  affected version)
- Steps to reproduce (minimal request payload + expected vs actual behavior)
- Your assessment of impact (what an attacker could do with this)
- Any suggested mitigation if you have one

You should receive an acknowledgement within **72 hours**.

## Disclosure window

We follow a **90-day coordinated disclosure** model:

1. Day 0 — report received, acknowledgement sent
2. Day 0-7 — issue confirmed, severity assessed, fix scoped
3. Day 7-60 — fix developed, tested, and merged
4. Day 60-90 — release published, CVE requested if applicable
5. Day 90+ — public advisory published

If the issue is being actively exploited in the wild, the window
compresses. If we go past 90 days without a fix, the reporter is free
to disclose publicly.

## Hardening posture

Things this project does NOT do, by design — none of these are
configurable, none can be turned on remotely:

- No telemetry
- No phone-home update beacons
- No anonymous metrics
- No vendor account model / no revocable license shapes
- No third-party inference SaaS calls (unless the operator explicitly
  configures one)

If you find a code path that violates any of these — even unintentionally
— please file it via the private report channel above. We treat sovereignty
violations as security issues.

## Out of scope

These are not security issues for this project:

- Vulnerabilities in upstream dependencies that we re-export (file them
  with the upstream — but please CC us if it affects us)
- Bugs in user-supplied model weights (we don't ship weights)
- Issues only reproducible against custom forks or modifications
- Theoretical attacks against a properly air-gapped deployment
