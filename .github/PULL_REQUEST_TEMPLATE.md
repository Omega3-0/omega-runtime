<!--
  Thanks for sending a PR. Fill out the checklist below — every item
  exists because of a past regression. CI will reject PRs that fail
  the automated subset.
-->

## Summary

<!-- One or two sentences. What does this change, and why? -->

## Type of change

- [ ] Bug fix (regression test included)
- [ ] New feature
- [ ] Performance improvement
- [ ] Refactor (no behavior change)
- [ ] Documentation only
- [ ] Build / CI

## Related issue

<!-- "Fixes #123" or "Refs #456" if applicable -->

## Test plan

<!-- How did you verify this works? Include CLI invocations + expected output. -->

- [ ] `pytest tests/` passes locally
- [ ] `ruff check .` passes
- [ ] `ruff format --check .` passes

## Regression test

<!-- For bug fixes: paste the test that would have FAILED before your fix. -->

## Sovereignty check

- [ ] No telemetry / phone-home / update beacons added
- [ ] No third-party SaaS calls added (unless operator-configured)
- [ ] No new credentials / API keys / secrets baked into the bundle

## OpenAI-spec check (skip if not touching `/v1/*`)

- [ ] If you touched `chat.completion.chunk` shape, verified single
      `chatcmpl-<uuid>` id across the stream + single `finish_reason`
      at end
- [ ] If you touched `stream_options`, verified the dedicated usage
      tail still fires before `[DONE]`
- [ ] If you touched `response_format`, verified JSON-mode contract
      enforcement still 502s on invalid model output

## Windows compatibility

- [ ] PR doesn't break the Windows build (`scripts/build_windows.ps1`)
- [ ] PR doesn't break the daemon spawn (PowerShell → `Server.exe daemon` →
      child must stay alive + serve `/health`)

## Documentation

- [ ] Public API changes documented in `docs/api_reference.md`
- [ ] Operator-visible changes documented in `docs/operator_handbook.md`
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
