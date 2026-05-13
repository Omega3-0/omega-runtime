# Omega Runtime Studio — Operator Handbook

Run-and-deploy playbook for operators shipping Omega Runtime Studio.
Recipe-oriented; if you want the full API surface, see
`docs/api_reference.md`. If you want the why / market positioning, see
`README.md`.

---

## 1. First-time setup

### From a release bundle (`dist\Omega3.0-portable\`)

1. Unzip anywhere — bundle is fully self-contained; no installer.
2. Drop GGUF files into `<bundle>\models\` (the bundled `models\` folder
   is empty by default, you populate it).
3. Double-click `Omega3.0-portable.exe` — the GUI starts and points at
   the bundled `Server.exe`.
4. Hit `Ctrl+Shift+S` in the GUI to start the API on
   `http://127.0.0.1:11434/v1` (change port under Settings → Server).
5. Confirm health: `curl http://127.0.0.1:11434/health` returns
   `{"status":"ok"}`.

### Headless / server-only deployment

1. Unzip the bundle on the host.
2. From a console: `Omega3.0-portable-Server.exe serve --host 0.0.0.0 --port 11434`
3. To run detached: `Omega3.0-portable-Server.exe daemon --host 0.0.0.0 --port 11434`
   — writes PID/log files under `%LOCALAPPDATA%\Omega3Portable\daemon\`.
4. Stop the daemon: `Omega3.0-portable-Server.exe daemon-stop`
5. Set `OMEGA_API_KEY=<your-key>` in the environment before starting to
   gate `/v1` + `/admin` behind bearer auth.

### Where files live

| Purpose | Path |
|---------|------|
| Bundle root (binaries + models folder) | wherever you unzipped — set via `OMEGA_BUNDLE_ROOT` if you want explicit |
| Per-user model registry, settings, hub jobs | `%LOCALAPPDATA%\Omega3Portable\` |
| Daemon PID + log | `%LOCALAPPDATA%\Omega3Portable\daemon\` |
| HF download cache | `%LOCALAPPDATA%\Omega3Portable\models\` |
| Vendor accelerator DLLs | `<bundle>\vendor\accelerators\bin\` (or legacy `vendor\lemonade\bin`) |
| Backend profile snapshot | `%LOCALAPPDATA%\Omega3Portable\backend_profile.json` |

---

## 2. Configuration

### Environment variables (full list)

See `docs/api_reference.md` § "Environment variables" for the canonical
list and defaults. The two most operators-care-about ones:

- `OMEGA_API_KEY` — bearer token required on `/v1/*` + `/admin/*`.
  Leave unset on a single-operator LAN box; set on any multi-tenant or
  public-facing deployment.
- `OMEGA_DRAIN_TIMEOUT_S` — how long the server waits for in-flight
  requests on shutdown before forcing exit (default 30s). Bump higher
  if long embeddings or large completions are common.

### Build variant detection

`/v1/version` reports the build variant (`cpu` / `cuda` / `vulkan` / `dml`).
Each variant ships separately:

- **cpu** (default) — works everywhere, no GPU required.
- **cuda** — NVIDIA GPU with CUDA 12.1+ runtime.
- **vulkan** — AMD / Intel / NVIDIA via Vulkan (cross-vendor GPU acceleration).
- **dml** — DirectML (Windows-native GPU acceleration for AMD/Intel/NVIDIA).

Mismatch (e.g., you ship `cuda` variant but the host has no NVIDIA card)
typically results in the model loading on CPU. Check `backend_snapshot`
on `/v1/models` to see what providers ORT actually has available.

### Tuning the resource manager

| Setting | Default | Operator concern |
|---------|---------|------------------|
| `max_concurrent_models` | 3 | How many GGUF handles can be resident at once. Bumping past 3 on a 16GB system is rarely productive. |
| `lru_eviction_enabled` | true | Set false to require manual `/admin/models/{id}/unload` before loading anything past `max_concurrent_models`. |
| `pinned` (per-model) | false | Pinned models are never evicted — even when overflow occurs. If every model is pinned and overflow exists, see eviction logs (next section). |

Adjust via `PATCH /v1/studio/models/{id}` (UI overrides) or directly in
the registry file at `%LOCALAPPDATA%\Omega3Portable\registry.json`.

---

## 3. Day-to-day operations

### Loading a model

GUI: click the model in the sidebar, then "Load". Headless:

```
curl -X POST http://127.0.0.1:11434/admin/models/<id>/load
```

Loading is serialized per model — concurrent requests against the same
model wait for the first load to finish, then share the handle.

### Downloading from Hugging Face

```
curl -X POST http://127.0.0.1:11434/v1/models/hub/download \
  -H "Content-Type: application/json" \
  -d '{"repo_id":"Qwen/Qwen2.5-7B-Instruct-GGUF","filename":"qwen2.5-7b-instruct-q4_k_m.gguf"}'
```

Returns `{job_id, dest_dir, poll}`. Poll the `poll` URL:

```
curl http://127.0.0.1:11434/v1/models/hub/download/<job_id>/status
```

Response includes `bytes_done`, `bytes_total`, `rate_mbps`, `eta_seconds`
in addition to legacy `progress`. The hub uses HF's resumable download
path; restart-safe even across server restarts (`.partial` file).

`HF_TOKEN` (or `HUGGING_FACE_HUB_TOKEN`) in the server env unlocks gated
models.

### Chat against a model

Any OpenAI-compatible client works. Direct curl:

```
curl http://127.0.0.1:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "<id-from-/v1/models>",
    "messages": [{"role":"user","content":"hello"}],
    "stream": true,
    "stream_options": {"include_usage": true}
  }'
```

### Switching models without restart

Just call `/admin/models/<old>/unload` then `/admin/models/<new>/load`,
or let LRU eviction handle it automatically on the next chat request.

---

## 4. Troubleshooting

### "Bundle won't start"

Most common: missing C runtime DLL (Visual Studio 2015-2022 Redistributable).
Install vcredist x64 from microsoft.com.

If `Omega3.0-portable-Server.exe serve` exits silently with no console
output, run it under `cmd /k` to keep the console open, or check
`%LOCALAPPDATA%\Omega3Portable\daemon\daemon.log`.

### "Model loads but chat returns 500"

Check that the model is GGUF / GGML format. ONNX and other formats are
not yet wired to chat (see `docs/api_reference.md` for the supported
matrix). Try `/v1/models` to confirm the format the server detected.

### "Streaming hangs partway"

Two likely causes:

1. **Inference timeout fired.** Default is 300s per generation chunk.
   Look for `inference stream timed out after Ns` in logs and bump
   `OMEGA_INFERENCE_TIMEOUT_S` if needed.
2. **Client disconnect not detected.** When using a custom HTTP client,
   ensure you process SSE chunks promptly; the server closes the
   underlying generator when the connection drops, but only at the next
   yield boundary.

### "I keep seeing 503 service_draining"

The server is in graceful drain. Either:

- Wait for it to finish shutting down, then restart, OR
- If it's stuck draining a hung request, send another SIGTERM /
  Ctrl+Break to force exit, OR
- Bump `OMEGA_DRAIN_TIMEOUT_S` if your typical request times legitimately
  exceed 30s.

`/health` still returns `200` during drain — use it to differentiate
"server is up but draining" from "server is dead".

### "Eviction is thrashing"

Look for repeated `eviction:` lines in logs with low `idle_s` (under a
few seconds). Possible causes:

- `max_concurrent_models` is too low for your workload — bump it if you
  have RAM headroom.
- Too many pinned models — if `eviction_blocked_by_pins` appears in
  logs, you're at capacity with pinned-only models and any new load is
  forced over the limit.

### "Builds are succeeding but binaries don't work"

The Windows build pipeline has a post-PyInstaller smoke gate
(`scripts/build_windows.ps1`) that launches `Server.exe` against a
random high port and probes `/health`. If smoke fails the script
renames the broken EXE to `.broken` and exits with code 2.

Skip the gate for fast iteration: `-SkipSmoke` flag or
`$env:OMEGA_BUILD_SKIP_SMOKE=1`.

A successful build emits the success marker file
`<dist>\build-smoke.log.ok`. Absence of that file post-build is a
build defect — re-run.

### "Request was rejected with 413"

Body exceeded `OMEGA_MAX_REQUEST_BYTES` (default 10 MiB). Bump the env
var or compress / chunk the input.

### "How do I correlate a slow request with its model load?"

Every response carries `X-Request-ID`. Every log record emitted while
serving that request is stamped with the same `request_id` field
(thanks to the contextvar-based filter). Trace forensically:

```
grep "<request-id>" omega_studio.log | sort | head -200
```

You'll see the request enter, any eviction it triggered (with its own
`reason=preload_overflow trigger=<model>`), the load duration, the
generation, and exit.

---

## 5. Deployment scenarios

### Laptop / single-operator (default)

- GUI mode, no API key, default port.
- `OMEGA_VARIANT=cuda` if you have an NVIDIA card; `cpu` otherwise.
- Model files in `<bundle>\models\` or in `%LOCALAPPDATA%\Omega3Portable\models\`.

### Internal team server

- Run as a Windows service or scheduled task launching
  `Omega3.0-portable-Server.exe daemon`.
- Set `OMEGA_API_KEY` and distribute via your secrets store.
- Open the listening port (11434) only on the internal subnet.
- Consider a reverse proxy (Caddy / nginx) for TLS termination and
  request rate limiting — Studio does not implement either.
- Bump `max_concurrent_models` per the host RAM budget.

### Air-gapped offline lab

- Use `-PostSyncVendor` on the build script to bundle the vendor DLL
  tree into the dist.
- Pre-populate models in `<bundle>\models\` before zipping.
- Disable HF download paths (operationally — there's no kill switch,
  but `huggingface_hub` will simply fail to reach the network).

---

## 6. Backup & migration

What to back up:

- `%LOCALAPPDATA%\Omega3Portable\registry.json` — model list, settings.
- `%LOCALAPPDATA%\Omega3Portable\models\` — downloaded GGUF files
  (these are the big payloads).
- `%LOCALAPPDATA%\Omega3Portable\hub_jobs.sqlite` — download history
  (optional; safe to drop for a clean slate).
- `%LOCALAPPDATA%\Omega3Portable\backend_profile.json` — accelerator
  snapshot (regenerated on startup; safe to drop).

Migrating to a new host:

1. Install the same bundle variant on the new host.
2. Copy `%LOCALAPPDATA%\Omega3Portable\` over.
3. Start the server; on first boot it rescans `model_folders` and
   updates the registry to match the new paths (registry persists
   absolute paths so adjust if your `models\` folder moved).

---

## 7. Observability cheat sheet

What to grep for in `omega_studio.log`:

| Pattern | Meaning |
|---------|---------|
| `eviction: model=...` | A model was evicted; structured fields show why |
| `eviction_blocked_by_pins:` | Capacity exceeded but all loaded models are pinned — manual intervention needed |
| `eviction_failed: ...` | Unload raised an exception (handle held by an active stream) |
| `stream_disconnected: ...` | Client dropped mid-stream; iterator was closed cleanly |
| `inference timed out after ...` | `OMEGA_INFERENCE_TIMEOUT_S` fired; bump if your model is slow |
| `shutdown: draining N in-flight ...` | Graceful drain started |
| `shutdown: drain timeout ...` | Drain took longer than `OMEGA_DRAIN_TIMEOUT_S`; some requests forced |

Every line carries the request_id of whatever drove the work (eviction,
stream, etc.) so cross-correlation is trivial: grep one ID, see one
request's full history.

---

## 8. When to ask for help

If you hit any of these, file a bug with the captured logs:

- Build smoke gate fails on a clean checkout
- `/v1/chat/completions` 500s with no clear error chain in the log
- Eviction never fires even when `loaded_ids() > max_concurrent_models`
- Drain takes the full timeout on a server with no active load (suggests
  a stuck request)
- The same `request_id` appears in logs from two different requests
  (would indicate the contextvar / middleware ordering broke)

Each of these has dedicated test coverage in `tests/` — if a bug
reproduces and the test still passes, the test is the next thing to
update.
