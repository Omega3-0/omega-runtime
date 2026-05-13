# Changelog

All notable changes to Omega Runtime Studio are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-05-13

Initial public release. Sovereign Windows-first local-model control plane:
FastAPI `/v1` API + PySide6 GUI, no third-party inference SaaS.

### Added

- **OpenAI-compatible API surface** — `/v1/chat/completions`,
  `/v1/embeddings`, `/v1/models`, `/v1/version`, `/health`, plus
  Studio-only management endpoints under `/v1/studio/*` and
  `/admin/*`.
- **Streaming spec compliance** — SSE chunks share one
  `chatcmpl-<uuid>` per completion, single `finish_reason` at end,
  dedicated `stream_options.include_usage` tail chunk synthesized
  server-side via tokenizer when llama-cpp doesn't natively emit it.
- **Per-request observability** — `X-Request-ID` allocated/echoed on
  every response (including auth-rejected + drain-rejected); logging
  filter stamps `record.request_id` on every record served for that
  request.
- **Graceful drain shutdown** — in-flight request counter + 503
  `service_draining` for new `/v1/*` traffic during shutdown; `/health`
  stays open so monitoring sees the state.
- **Resource manager observability** — every eviction logs with
  `reason=` (`preload_overflow` / `postload_overflow`), `trigger=`,
  `idle_s=`, `pinned_skipped=`; pin-blocked overflow emits a separate
  warning.
- **Hub download progress** — `bytes_done` / `bytes_total` /
  `rate_mbps` / `eta_seconds` on `GET /v1/models/hub/download/{id}/status`,
  averaged over a 1.5s sliding window so polling clients see a stable
  number. Events deque sampled at ≥1% movement so a 64-deep ring
  buffer covers the full download history.
- **JSON-mode enforcement** — `response_format` validated upfront
  (400 on bad shape); `json_object` / `json_schema` modes parse the
  model output and attach the parsed dict under `choices[].omega.json`;
  502 on contract violation rather than handing the client a broken
  string.
- **Tool-call deterministic repair** — code-fence wrappers, single
  quotes, trailing commas, and Python literal booleans
  (`True`/`False`/`None`) in `tool_calls[].function.arguments`
  auto-repaired before reaching the client. Audit trail attached
  under `choices[].omega.tool_calls_repaired`.
- **SSE cleanup paths** — engine iterator closed in `finally` so
  llama-cpp's sequence slot + KV cache release immediately on normal
  exit, error, OR client disconnect.
- **ONNX foundation** — `ONNXBackend` wrapper with provider ordering
  (VitisAI → CUDA → DML → CPU); `ONNXEmbedder` pipeline (tokenize →
  infer → pool → L2-normalize); `/v1/embeddings` accepts `.onnx`
  format models with per-model pooling strategy (mean / cls / max)
  configurable via registry `ui_overrides`.
- **CLI daemon lifecycle** — `serve` foreground, `daemon` headless
  (Windows CREATE_NO_WINDOW + CREATE_NEW_PROCESS_GROUP),
  `daemon-stop` with `/T` tree-kill so PyInstaller bootloader +
  re-exec'd grandchild both terminate cleanly. `_MEIPASS2` env
  scrubbed before child spawn to prevent parent→child onefile
  extraction collision.
- **Build pipeline** — `scripts/build_windows.ps1` (dev) +
  `scripts/build_release.ps1` (retail); post-build smoke gate
  launches the bundle and verifies `/health` before declaring
  success; both scripts hard-abort on non-zero PyInstaller exit
  (replaces the previous false-positive `Invoke-Expression`
  pattern that hid build failures).
- **Variant matrix** — `cpu` / `cuda` / `vulkan` / `dml` build
  variants surfaced via `/v1/version` and `_resolve_backend()`.
- **Code signing infrastructure** — `scripts/sign_windows.ps1`
  with post-sign `signtool verify` step + password redaction in
  logs; `scripts/release_manifest.ps1` for SHA-256 release
  manifest; integrated into `build_release.ps1` via
  `OMEGA_SIGN_CERT_*` env vars.
- **Docs** — operator handbook, full API reference, code-signing
  guide.

### Test coverage at release

164 tests covering: middleware, drain, eviction observability, hub
download progress, JSON-mode enforcement, tool-call repair, ONNX
embedding pipeline, concurrent stress, SSE cleanup, streaming usage
synthesis, daemon-stop tree-kill, request_id correlation, model-id
override (no path leaks). Every audit finding + every live-test
finding has a permanent regression test.

[Unreleased]: https://github.com/Omega3-0/omega-runtime/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Omega3-0/omega-runtime/releases/tag/v0.1.0
